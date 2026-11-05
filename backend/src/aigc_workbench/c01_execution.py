"""Synchronous C01 preparation/test execution. Never invokes a model or changes business state."""

from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from .application import TaskService
from .c01_context import (C01Error, applicable_rules, assemble_context, fingerprint, now,
                          resolve_effective_state, select_source)
from .c01_persistence import C01EvidenceStore
from .c01_prompt import compose_prompt, validate_composition
from .config import REPOSITORY_ROOT, Settings


def require_development(settings: Settings) -> None:
    if settings.environment != "development" or not settings.enable_c01_testing:
        raise C01Error("DEVELOPER_ENTRY_DISABLED", "ACCESS",
                       "仅 development 且 AIGC_ENABLE_C01_TESTING=1 可使用 C01 开发命令。")


class C01TestAdapter:
    label = "TEST_ONLY / MOCK_ADAPTER"
    version = "c01-byte-receipt-v1"

    def execute(self, text: str, image_bytes: bytes, image_reference: dict, mode: str) -> dict:
        if sha256(image_bytes).hexdigest() != image_reference["sha256"]:
            raise C01Error("ADAPTER_IMAGE_MISMATCH", "TEST_ADAPTER", "测试适配器收到错误图片字节。")
        if not text or not image_bytes:
            raise C01Error("ADAPTER_INPUT_EMPTY", "TEST_ADAPTER", "没有实际文字或图片字节。")
        if mode == "fail":
            raise C01Error("TEST_ADAPTER_FAILURE", "TEST_ADAPTER", "显式测试模式模拟适配器失败。")
        if mode != "success":
            raise C01Error("ADAPTER_MODE_INVALID", "TEST_ADAPTER", "不支持该测试模式。")
        return {
            "label": self.label, "adapter_version": self.version,
            "receipt": {"text_sha256": sha256(text.encode()).hexdigest(),
                        "image_sha256": sha256(image_bytes).hexdigest(),
                        "image_byte_count": len(image_bytes),
                        "source_asset_id": image_reference["source_asset_id"]},
            "visual_understanding": None, "AI_DERIVED": False,
            "model_api_call_attempted": False,
            "meaning": "只验证文字/真实图片字节的工程交接；没有视觉推理或商品观察结果。",
        }


class C01Service:
    def __init__(self, settings: Settings, tasks: TaskService, evidence: C01EvidenceStore,
                 source_root: Path = REPOSITORY_ROOT):
        require_development(settings)
        self.tasks, self.evidence, self.source_root = tasks, evidence, source_root

    def prepare(self, task_id: UUID, asset_id: UUID) -> dict:
        selection, image = select_source(self.tasks, task_id, asset_id)
        try:
            effective = resolve_effective_state(selection)
            context = assemble_context(selection, effective, applicable_rules(self.source_root))
        except C01Error:
            raise
        except Exception as exc:
            raise C01Error("CONTEXT_ASSEMBLY_FAILED", "CONTEXT_ASSEMBLY",
                           "有效状态或 Context 组装失败，未保存快照。") from exc
        snapshot_id = str(uuid4())
        try:
            prompt = compose_prompt(context, snapshot_id)
        except C01Error:
            raise
        except Exception as exc:
            raise C01Error("PROMPT_COMPOSITION_FAILED", "PROMPT_COMPOSITION",
                           "Prompt 组合失败，未保存快照。") from exc
        snapshot = {
            "id": snapshot_id, "created_at": now(), "capability": "C01",
            "task_id": str(task_id), "product_asset_id": str(asset_id),
            "source_selection": selection, "effective_state": effective,
            "context": context, "context_sha256": fingerprint(context), "prompt": prompt,
            "input_classification": "PLANNED MODEL INPUT", "actual_model_input": None,
            "model_api_call_attempted": False,
            "lifecycle": "只追加，不因后续业务变化重写；新准备重新解析，历史执行使用固定副本。",
        }
        try:
            return self.evidence.save_snapshot(snapshot, image)
        except C01Error:
            raise
        except Exception as exc:
            raise C01Error("SNAPSHOT_SAVE_FAILED", "SNAPSHOT_SAVE",
                           f"Snapshot {snapshot_id} 未确认可靠保存；核对历史及孤立副本，不自动重试。") from exc

    def snapshot_metadata(self, snapshot_id: UUID) -> dict:
        snapshot = self.evidence.load_snapshot(snapshot_id)
        if fingerprint(snapshot["context"]) != snapshot["context_sha256"]:
            raise C01Error("CONTEXT_INTEGRITY_FAILED", "EVIDENCE_QUERY", "Context 哈希不一致。")
        validate_composition(snapshot["prompt"], snapshot["context"], snapshot["id"])
        return snapshot

    def inspect_snapshot(self, snapshot_id: UUID) -> dict:
        snapshot = self.snapshot_metadata(snapshot_id)
        self.evidence.read_snapshot_image(snapshot)
        return snapshot

    def run_test(self, snapshot_id: UUID, operation_id: UUID, mode: str = "success",
                 adapter: C01TestAdapter | None = None) -> dict:
        if mode not in {"success", "fail"}:
            raise C01Error("ADAPTER_MODE_INVALID", "RUN_START", "仅允许 success / fail 测试。")
        # Persist the attempt before loading execution inputs so failures are traceable.
        selected_snapshot = self.evidence.load_snapshot(snapshot_id)
        run_id, created = self.evidence.start_run(snapshot_id, operation_id, mode)
        if not created:
            return self.inspect_run(run_id)
        running = self.evidence.inspect_run(run_id)
        adapter = adapter or C01TestAdapter()
        result, actual_input, error, invoked = None, None, None, False
        try:
            snapshot = self.snapshot_metadata(snapshot_id)
            image = self.evidence.read_snapshot_image(snapshot)
            planned = snapshot["prompt"]["planned_model_input"]
            actual_input = {
                "label": "ACTUAL TEST ADAPTER INPUT — NOT ACTUAL MODEL INPUT",
                "text_sha256": sha256(planned["text"].encode()).hexdigest(),
                "image_sha256": sha256(image).hexdigest(), "image_byte_count": len(image),
                "image_source_asset_id": snapshot["product_asset_id"],
                "image_representation": "RAW_BYTES_PASSED_TO_ADAPTER",
            }
            invoked = True
            result = adapter.execute(planned["text"], image, planned["images"][0], mode)
            if (result.get("label") != "TEST_ONLY / MOCK_ADAPTER" or result.get("AI_DERIVED") is not False
                    or result.get("visual_understanding") is not None
                    or result.get("model_api_call_attempted") is not False
                    or result.get("receipt", {}).get("image_sha256") != actual_input["image_sha256"]
                    or result.get("receipt", {}).get("text_sha256") != actual_input["text_sha256"]):
                raise C01Error("ADAPTER_RESULT_INVALID", "TEST_ADAPTER", "测试响应身份或输入收据不一致。")
        except C01Error as exc:
            error = exc.record()
        except Exception as exc:
            error = C01Error("TEST_EXECUTION_FAILED", "TEST_EXECUTION",
                             "测试执行失败；未将内部异常当作视觉理解。").record()
            error["cause_type"] = type(exc).__name__
        evidence = {
            "label": "TEST_ONLY EXECUTION", "adapter": adapter.label, "adapter_version": adapter.version,
            "run_id": str(run_id), "attempt_id": running["attempt"]["id"],
            "snapshot_id": str(snapshot_id), "snapshot_sha256": fingerprint(selected_snapshot),
            "started_at": running["attempt"]["started_at"], "finished_at": now(),
            "status": "FAILED" if error else "SUCCEEDED", "adapter_invoked": invoked,
            "actual_test_adapter_input": actual_input, "raw_test_result": result, "error": error,
            "actual_model_input": None, "actual_model_response": None,
            "model_api_call_attempted": False, "provider_usage": None, "provider_cost": None,
            "business_state_changed": False, "AI_DERIVED_created": False,
        }
        try:
            self.evidence.finish_run(run_id, evidence)
        except Exception as exc:
            try:
                self.evidence.mark_evidence_failure(run_id, evidence, type(exc).__name__)
            except Exception:
                raise C01Error("EVIDENCE_OUTCOME_UNKNOWN", "EVIDENCE_SAVE",
                               f"Run {run_id} 的证据/完成状态未确认，请查询；没有自动重跑。") from exc
        return self.inspect_run(run_id)

    def inspect_run(self, run_id: UUID) -> dict:
        record = self.evidence.inspect_run(run_id)
        snapshot = self.snapshot_metadata(UUID(record["run"]["snapshot_id"]))
        if record["evidence"] is not None and record["evidence"]["snapshot_sha256"] != fingerprint(snapshot):
            raise C01Error("EVIDENCE_INTEGRITY_FAILED", "EVIDENCE_QUERY", "Evidence 指向的快照哈希不一致。")
        record["snapshot"] = snapshot
        if record["run"]["status"] == "RUNNING":
            record["state_note"] = "尚未可靠完成，不保证进程仍存活；核对现有记录，不自动重跑。"
        try:
            self.evidence.read_snapshot_image(snapshot)
            record["snapshot_image_integrity"] = {"status": "VERIFIED"}
        except C01Error as exc:
            record["snapshot_image_integrity"] = {"status": "UNAVAILABLE", "error": exc.record()}
        return record
