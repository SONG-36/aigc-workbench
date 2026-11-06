"""Development-only, one-case Bailian comparison. Every stage is one shot; reruns fail closed."""

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import UUID, uuid5

from .application import TaskService
from .bailian_model import VISION_MODEL, IMAGE_MODEL, POLICY, selected_provider
from .c01_context import C01Error, canonical, fingerprint
from .c01_execution import C01Service, require_development
from .c01_persistence import C01EvidenceStore
from .c01_real import SNAPSHOT_ID, IMAGE_SHA256, SNAPSHOT_SHA256
from .c02_reference import C02RealService
from .c06_candidates import private_write
from .c06_generation import C06Service
from .c07_revision import C07Service
from .config import Settings, CredentialConfigError, load_provider_credentials
from .file_storage import LocalImageFiles
from .persistence import SQLiteTaskStore
from .quick_a_creation_decision import QuickACreationDecisionService
from .quick_a_sources import ProductUnderstandingService

TASK = UUID("940f28e9-09b8-427d-b501-ad57bfb886c9")
SLOT = UUID("15375a95-9da6-4faa-a267-4dcb4f03aaee")
PRODUCT = UUID("a452aaca-5bfe-4cb9-8bdb-9bd9bd3e8aa3")
REFERENCE = UUID("8d78275c-9f44-4c9e-b47f-1f15a82f9729")
REFERENCE_SHA = "d292fa625dfd4f86fa43ee0407dde9ead69dadd83a3b6ec1d8a903227ead4155"
DECISION = UUID("f906f29e-1530-482d-9b2e-58dc587e173a")
HANDOFF = Path(".runtime/creation-decision/20261105T054741Z-1ae91607-5d76-41cf-a77b-fd3469211557")
REVISION = "仅将主标题 Wide Voltage 改为深蓝色 #17365D，文字内容和其他元素保持不变。"


def services(settings):
    require_development(settings)
    if selected_provider() != "bailian":
        raise C01Error("PROVIDER_NOT_SELECTED", "ACCESS", "请显式设置 AIGC_MODEL_PROVIDER=bailian。")
    database = SQLiteTaskStore(settings.database_path)
    if not settings.database_path.is_file():
        raise C01Error("BUSINESS_DATABASE_MISSING", "ACCESS", "需要已有开发数据库；不初始化替代案例。")
    return C01Service(settings, TaskService(database, LocalImageFiles(settings.data_dir)),
                      C01EvidenceStore(database, settings.data_dir))


def verify_sources(base):
    task = base.tasks.get_draft(TASK)
    slot = next((s for s in task.slots if s.id == SLOT), None)
    if task.mode != "quick_a" or slot is None or slot.reference_asset_id != REFERENCE or slot.output_size != "1024x1536":
        raise C01Error("CASE_IDENTITY_CHANGED", "SOURCE_SELECTION", "真实图位、绑定或尺寸与对照案例不同。")
    sources = []
    for identity, role, expected in [(PRODUCT, "product_image", IMAGE_SHA256),
                                     (REFERENCE, "marketing_reference", REFERENCE_SHA)]:
        asset, content = base.tasks.read_asset(TASK, identity)
        if asset.role != role or sha256(content).hexdigest() != expected:
            raise C01Error("CASE_SOURCE_CHANGED", "SOURCE_SELECTION", "实际来源身份或哈希不一致。")
        sources.append({"asset_id": str(identity), "role": role, **asdict(asset.image)})
    snapshot = base.inspect_snapshot(UUID(SNAPSHOT_ID))
    if (fingerprint(snapshot) != SNAPSHOT_SHA256 or snapshot["context"]["operator_confirmed_information"] != []
            or base.evidence.read_snapshot_image(snapshot) != base.tasks.read_asset(TASK, PRODUCT)[1]):
        raise C01Error("CASE_SNAPSHOT_CHANGED", "SOURCE_SELECTION", "原S02快照或固定原图不一致。")
    return {"task_id": str(TASK), "slot_id": str(SLOT), "sources_in_order": sources,
            "s02_snapshot_sha256": fingerprint(snapshot), "operator_confirmed_information": [],
            "vision_model": VISION_MODEL, "image_model": IMAGE_MODEL,
            "C06_decision_authority": "EXISTING_GPT_HANDOFF_PLUS_SEPARATE_QUALITY_AMENDMENTS / PROPOSED",
            "C03_C04_C05_comparison_result_used_for_C06": False}


def export_run(directory, stage, run, base):
    """Private exact native response/request/snapshot alongside human-readable parsed output."""
    folder = directory / stage
    folder.mkdir(mode=0o700)
    snapshot = base.evidence.load_snapshot(UUID(run["snapshot_id"]))
    objects = {"run.json": run, "snapshot.json": snapshot,
               "actual-request.json": run["actual_model_input"],
               "actual-response.json": run["actual_model_response"],
               "structured-result.json": run["structured_output"]}
    for name, body in objects.items():
        private_write(folder / name, canonical(body).encode())
    private_write(folder / "prompt.txt", snapshot["prompt"]["planned_model_input"]["text"].encode())
    return {"run_id": run["run_id"], "attempt_id": run["attempt_id"], "operation_id": run["operation_id"],
            "snapshot_id": run["snapshot_id"], "status": run["status"], "http_status": run["response_http_status"],
            "duration_ms": run["duration_ms"], "usage": run["provider_usage"],
            "cost_estimate": run["usage_based_cost_estimate"], "evidence": str(folder.resolve()),
            "candidate": run.get("candidate"), "error": run.get("error")}


def run_case(settings, identity, *, execute=False):
    base = services(settings)
    report = verify_sources(base)
    credential = load_provider_credentials()
    report["key"] = credential
    product = ProductUnderstandingService(settings, base)
    c01 = product.preflight(UUID(SNAPSHOT_ID), VISION_MODEL)
    generator = C06Service(settings, base)
    # Revalidate original handoff before making any paid request. Never replace it with Qwen's new decision.
    handoff = base.source_root / HANDOFF
    generator._sources(DECISION, handoff)
    report.update(precheck="PASSED" if credential == "KEY_CONFIGURED" else "KEY_MISSING",
                  C01_request_sha256=c01["request_sha256"], paid_calls=0)
    if not execute or credential != "KEY_CONFIGURED":
        return report
    if (not settings.enable_c01_real or os.environ.get("AIGC_ENABLE_C06_REAL") != "1"
            or os.environ.get("AIGC_ENABLE_C07_REAL") != "1"):
        raise C01Error("BAILIAN_EXECUTION_DISABLED", "ACCESS", "必须在任何调用前显式启用C01/C06/C07真实执行。")
    directory = settings.data_dir.parent / "bailian" / str(identity)
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.mkdir(mode=0o700)  # Existing experiment never starts new paid requests, even after a crash.
    report.update(experiment_id=str(identity), stages=[], evidence=str(directory.resolve()))
    private_write(directory / "dispatch-manifest.json", canonical({**report,
        "operations": {stage: str(uuid5(identity, stage)) for stage in ("C01", "C02", "C03_C04_C05", "C06", "C07")},
        "maximum_paid_calls": 5, "automatic_retries": 0,
        "note": "Presence blocks automatic rerun. Consult durable Runs after uncertain outcomes."}).encode())

    def stage(name, service, snapshot, model, limit=4096):
        run = service.run(UUID(snapshot["id"]), model, uuid5(identity, name), confirmed=True,
                          max_cost_usd=POLICY, **({"max_output_tokens": limit} if name not in {"C06", "C07"} else {}))
        summary = export_run(directory, name, run, base)
        report["stages"].append(summary)
        report["paid_calls"] += int(run["model_api_call_attempted"])
        private_write(directory / (name + "-summary.json"), canonical(summary).encode())
        if run["status"] != "SUCCEEDED":
            raise StageStopped(report)
        return run

    try:
        first = stage("C01", product, c01["snapshot"], VISION_MODEL)
        reference = C02RealService(settings, base)
        second = stage("C02", reference, reference.prepare(TASK, SLOT, REFERENCE), VISION_MODEL)
        decision = QuickACreationDecisionService(settings, base, workspace=True)
        stage("C03_C04_C05", decision, decision.prepare(TASK, SLOT, PRODUCT, REFERENCE,
              UUID(first["run_id"]), UUID(second["run_id"])), VISION_MODEL, 8192)
        image = stage("C06", generator, generator.prepare(DECISION, handoff, IMAGE_MODEL), IMAGE_MODEL)
        revision = C07Service(settings, base)
        stage("C07", revision, revision.prepare(TASK, SLOT, UUID(image["candidate"]["image_version_id"]), REVISION), IMAGE_MODEL)
    except StageStopped:
        report["result"] = "STOPPED_ON_ACTUAL_STAGE_FAILURE / NO_RETRY"
    except Exception:
        # Do not lose completed stages or let local saving trouble trigger another provider call.
        private_write(directory / "interrupted-report.json", canonical(report).encode())
        raise
    report.setdefault("result", "REAL_OUTPUTS_SAVED / OPERATOR_REVIEW_PENDING")
    private_write(directory / "report.json", canonical(report).encode())
    if fingerprint(base.evidence.load_snapshot(UUID(SNAPSHOT_ID))) != SNAPSHOT_SHA256:
        raise C01Error("HISTORICAL_SNAPSHOT_CHANGED", "VERIFY", "历史快照校验失败。")
    return report


class StageStopped(Exception):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", type=UUID, required=True)
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_case(Settings.from_environment(), args.experiment_id, execute=args.run_once)
        print(json.dumps(result, ensure_ascii=False, indent=2))  # No prompts, base64, signed URLs or keys.
        return 0 if result.get("precheck") == "PASSED" and "STOPPED" not in result.get("result", "") else 1
    except (C01Error, CredentialConfigError, OSError, ValueError) as exc:
        error = exc.record() if isinstance(exc, C01Error) else {"code": type(exc).__name__, "message": "本地配置或保存阻断；先查询Run，不自动重试。"}
        print(json.dumps({"error": error}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
