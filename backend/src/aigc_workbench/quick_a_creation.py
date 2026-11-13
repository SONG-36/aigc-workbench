"""One explicit new-task creation use case on the existing single-worker Runtime."""

from hashlib import sha256
import json
from uuid import UUID, uuid5

from .c01_context import C01Error, fingerprint
from .c01_execution import C01Service
from .c01_model import DEFAULT_UNDERSTANDING_MODEL as VISION_MODEL, VISION_OUTPUT_TOKENS, DECISION_OUTPUT_TOKENS
from .c01_persistence import C01EvidenceStore
from .c01_model_store import C01ModelStore
from .c02_reference import C02RealService
from .c06_generation import C06Service, MODEL as IMAGE_MODEL
from .quick_a_creation_decision import QuickACreationDecisionService
from .quick_a_sources import ProductUnderstandingService, current_sources, usable_run


class StageStopped(C01Error):
    def __init__(self, stage, run):
        super().__init__('QUICK_A_STAGE_STOPPED', stage, f"{stage}执行{run['status']}，已保存证据；未继续制作，不自动重试。")
        self.run = run

    def record(self):
        return {**super().record(), 'outcome_unknown': self.run['status'] in {'RUNNING', 'OUTCOME_UNKNOWN'},
                'run_id': self.run['run_id'], 'attempt_id': self.run['attempt_id'], 'cause': self.run.get('error')}


class QuickACreation:
    def __init__(self, settings, tasks, database, dispatcher, *, responses_transport=None, image_transport=None):
        self.settings, self.database, self.dispatcher = settings, database, dispatcher
        self.service = C01Service(settings, tasks, C01EvidenceStore(database, settings.data_dir))
        self.runs = C01ModelStore(database)
        self.responses_transport, self.image_transport = responses_transport, image_transport
        self.test_mode = responses_transport is not None or image_transport is not None
        if self.test_mode and (responses_transport is None or image_transport is None
                or settings.environment != 'development' or responses_transport.label != 'OFFLINE_TRANSPORT_TEST'
                or image_transport.label != 'OFFLINE_TRANSPORT_TEST'):
            raise C01Error('QUICK_A_TRANSPORT_INCOMPLETE', 'ACCESS', '完整链路测试必须显式注入两种隔离测试适配器。')

    def _assert_sources(self):
        state, _ = current_sources(self.service, UUID(self.root['task_id']), UUID(self.root['slot_id']))
        expected = {key: value for key, value in self.root['context'].items() if key != 'capability_specific_inputs'}
        if state != expected:
            raise C01Error('QUICK_A_INPUT_CHANGED', 'EFFECTIVE_STATE', '执行期间素材、图位或有效要求已变化；未继续调用。')

    def _progress(self, stage):
        self.dispatcher.progress(self.operation, stage, self.steps)

    def _cached(self, capability, service):
        state = self.root['context']
        with self.database.connection() as db:
            rows = db.execute("SELECT r.id,s.body_json FROM c01_model_runs r JOIN c01_snapshots s ON s.id=r.snapshot_id "
                              "WHERE s.task_id=? AND json_extract(s.body_json,'$.capability')=? ORDER BY r.rowid DESC",
                              (state['task_id'], capability)).fetchall()
        for row in rows:
            snapshot = json.loads(row['body_json'])
            if capability == 'C01' and snapshot.get('product_asset_id') != state['product_asset_id']:
                continue
            if capability == 'C02' and (snapshot.get('reference_asset_id') != state['reference_asset_id']
                                        or snapshot.get('slot_id') != state['slot_id']):
                continue
            run = self.runs.inspect(UUID(row['id']))
            expected_transport = 'OFFLINE_TRANSPORT_TEST' if self.test_mode else 'REAL_PROVIDER_TRANSPORT'
            if run['transport_label'] != expected_transport or run['requested_model'] != VISION_MODEL:
                continue
            try:
                flight = service.preflight(UUID(snapshot['id']), VISION_MODEL, VISION_OUTPUT_TOKENS)
                if fingerprint(snapshot) != run['snapshot_sha256'] or flight['request_sha256'] != run['request_sha256']:
                    continue
                if capability == 'C02' and any(sha256((self.service.source_root / item['file']).read_bytes()).hexdigest()
                                               != item['sha256'] for item in snapshot['context']['applicable_rules']):
                    continue
            except (C01Error, OSError, ValueError):
                continue
            if run['status'] in {'RUNNING', 'OUTCOME_UNKNOWN'}:
                self.steps.append({'capability': capability, 'snapshot_id': snapshot['id'], 'run_id': run['run_id'],
                    'attempt_id': run['attempt_id'], 'operation_id': run['operation_id'], 'status': run['status'], 'reused': False})
                self._progress(capability)
                raise StageStopped(capability, run)
            if run['status'] == 'SUCCEEDED' and usable_run(run, self.test_mode):
                return run
            return None  # A new explicit operation may retry a known failure, never an unknown result.
        return None

    def _step(self, capability, service, prepare, *, model=VISION_MODEL, tokens=VISION_OUTPUT_TOKENS, cache=False):
        self._assert_sources()
        self._progress(capability)
        cached = self._cached(capability, service) if cache else None
        snapshot = self.service.evidence.load_snapshot(UUID(cached['snapshot_id'])) if cached else prepare()
        operation = self.operation if capability == 'C06' else uuid5(self.operation, capability)
        step = {'capability': capability, 'snapshot_id': snapshot['id'],
                'operation_id': cached['operation_id'] if cached else str(operation),
                'status': 'RUNNING', 'reused': bool(cached), 'run_id': None, 'attempt_id': None}
        self.steps.append(step); self._progress(capability)
        if cached:
            run = cached
            if capability == 'C01':
                service._debug_c01_return(snapshot, run, reused=True)
        elif capability == 'C06':
            run = service.run(UUID(snapshot['id']), model, operation, confirmed=True, max_cost_usd=self.budget,
                              transport=self.image_transport)
        else:
            run = service.run(UUID(snapshot['id']), model, operation, confirmed=True, max_cost_usd=self.budget,
                              max_output_tokens=tokens, transport=self.responses_transport)
        step.update(status=run['status'], run_id=run['run_id'], attempt_id=run['attempt_id'])
        self._progress(capability)
        if run['status'] != 'SUCCEEDED':
            raise StageStopped(capability, run)
        return run

    def execute(self, snapshot_id, operation_id, budget):
        self.root = self.service.evidence.load_snapshot(snapshot_id)
        if (self.root['capability'] != 'QUICK_A_CREATION' or fingerprint(self.root['context']) != self.root['context_sha256']
                or self.root['models'] != {'understanding': VISION_MODEL, 'image': IMAGE_MODEL}):
            raise C01Error('QUICK_A_SNAPSHOT_INVALID', 'BUSINESS_ROUTING', '完整制作操作快照不一致。')
        self.operation, self.budget, self.steps = operation_id, budget, []
        self._progress('PREPARING'); self._assert_sources()
        state = self.root['context']
        task, slot = UUID(state['task_id']), UUID(state['slot_id'])
        product, reference = UUID(state['product_asset_id']), UUID(state['reference_asset_id'])
        c01 = ProductUnderstandingService(self.settings, self.service)
        c01.debug_slot_id = slot
        product_run = self._step('C01', c01, lambda: self.service.prepare(task, product,
            source_reason='Workspace显式生成操作解析当前最新上传商品图；界面展示实际来源。'), cache=True)
        c02 = C02RealService(self.settings, self.service)
        reference_run = self._step('C02', c02, lambda: c02.prepare(task, slot, reference), cache=True)
        decision = QuickACreationDecisionService(self.settings, self.service, workspace=True, test_mode=self.test_mode)
        decision_run = self._step('C03_C04_C05', decision, lambda: decision.prepare(task, slot, product, reference,
            UUID(product_run['run_id']), UUID(reference_run['run_id'])), tokens=DECISION_OUTPUT_TOKENS)
        c06 = C06Service(self.settings, self.service, test_mode=self.test_mode)
        result = self._step('C06', c06, lambda: c06.prepare_current_decision(decision_run), model=IMAGE_MODEL)
        self._progress('REVIEW_PENDING')
        return result
