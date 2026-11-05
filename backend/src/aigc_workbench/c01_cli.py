"""Opt-in local developer CLI. Real requests require a separate explicit paid-call gate."""

import argparse
import json
import sqlite3
import sys
from uuid import UUID

from .application import TaskNotFound, TaskService
from .c01_context import C01Error
from .c01_execution import C01Service, require_development
from .c01_persistence import C01EvidenceStore
from .c01_prompt import readable_request
from .c01_real import C01RealService, preflight_report
from .c01_model_store import C01ModelStore
from .c01_model import OpenAIResponsesTransport
from .c02_reference import C02RealService
from .config import CredentialConfigError, Settings, load_openai_credentials
from .file_storage import LocalImageFiles
from .persistence import SQLiteTaskStore


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="C01 developer inspection, test execution and gated real-model experiment.")
    commands = cli.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--task-id", type=UUID, required=True)
    prepare.add_argument("--asset-id", type=UUID, required=True)
    inspect = commands.add_parser("inspect-snapshot")
    inspect.add_argument("--snapshot-id", type=UUID, required=True)
    inspect.add_argument("--format", choices=["json", "readable"], default="json")
    run = commands.add_parser("run")
    run.add_argument("--snapshot-id", type=UUID, required=True)
    run.add_argument("--operation-id", type=UUID, required=True)
    run.add_argument("--adapter-mode", choices=["success", "fail"], default="success")
    inspect_run = commands.add_parser("inspect-run")
    inspect_run.add_argument("--run-id", type=UUID, required=True)
    history = commands.add_parser("history")
    history.add_argument("--task-id", type=UUID, required=True)
    for command in ("preflight-real", "run-real"):
        real = commands.add_parser(command)
        real.add_argument("--snapshot-id", type=UUID, required=True)
        real.add_argument("--model", required=True)
        real.add_argument("--max-output-tokens", type=int, default=4096)
        if command == "run-real":
            real.add_argument("--operation-id", type=UUID, required=True)
            real.add_argument("--confirm-paid-call", action="store_true")
            real.add_argument("--max-cost-usd", required=True)
    model_run = commands.add_parser("inspect-model-run")
    model_run.add_argument("--run-id", type=UUID, required=True)
    commands.add_parser("credential-status")
    model_access = commands.add_parser("check-model-access")
    model_access.add_argument("--model", required=True)
    prepare_c02 = commands.add_parser("prepare-c02")
    prepare_c02.add_argument("--task-id", type=UUID, required=True)
    prepare_c02.add_argument("--slot-id", type=UUID, required=True)
    prepare_c02.add_argument("--reference-asset-id", type=UUID, required=True)
    for command in ("preflight-c02", "run-c02"):
        c02 = commands.add_parser(command)
        c02.add_argument("--snapshot-id", type=UUID, required=True)
        c02.add_argument("--model", required=True)
        c02.add_argument("--max-output-tokens", type=int, default=4096)
        if command == "run-c02":
            c02.add_argument("--operation-id", type=UUID, required=True)
            c02.add_argument("--confirm-paid-call", action="store_true")
            c02.add_argument("--max-cost-usd", required=True)
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        settings = Settings.from_environment()
        require_development(settings)  # Check before DB access, migration or directory creation.
        if args.command in {"credential-status", "preflight-real", "run-real", "check-model-access",
                            "preflight-c02", "run-c02"}:
            key_status = load_openai_credentials()
            if args.command == "credential-status":
                print(key_status)
                return 0 if key_status == "KEY_CONFIGURED" else 2
            if args.command == "check-model-access":
                result = OpenAIResponsesTransport().check_model_access(args.model)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["classification"] == "MODEL_METADATA_ACCESSIBLE" else 2
        if not settings.database_path.is_file():
            raise C01Error("BUSINESS_DATABASE_MISSING", "SOURCE_SELECTION", "需要现有 S01 数据库。")
        database = SQLiteTaskStore(settings.database_path)
        if args.command in {"prepare", "run", "prepare-c02"}:
            database.initialize()
        evidence = C01EvidenceStore(database, settings.data_dir)
        service = C01Service(settings, TaskService(database, LocalImageFiles(settings.data_dir)), evidence)
        if args.command == "prepare":
            result = service.prepare(args.task_id, args.asset_id)
        elif args.command == "inspect-snapshot":
            result = service.inspect_snapshot(args.snapshot_id)
            if args.format == "readable":
                print(readable_request(result))
                return 0
        elif args.command == "run":
            result = service.run_test(args.snapshot_id, args.operation_id, args.adapter_mode)
        elif args.command == "inspect-run":
            result = service.inspect_run(args.run_id)
        elif args.command == "preflight-real":
            result = preflight_report(C01RealService(settings, service).preflight(
                args.snapshot_id, args.model, args.max_output_tokens))
        elif args.command == "run-real":
            result = C01RealService(settings, service).run(
                args.snapshot_id, args.model, args.operation_id, confirmed=args.confirm_paid_call,
                max_cost_usd=args.max_cost_usd, max_output_tokens=args.max_output_tokens)
        elif args.command == "inspect-model-run":
            result = C01ModelStore(database).inspect(args.run_id)
        elif args.command == "prepare-c02":
            result = C02RealService(settings, service).prepare(args.task_id, args.slot_id, args.reference_asset_id)
        elif args.command == "preflight-c02":
            result = preflight_report(C02RealService(settings, service).preflight(
                args.snapshot_id, args.model, args.max_output_tokens))
        elif args.command == "run-c02":
            result = C02RealService(settings, service).run(
                args.snapshot_id, args.model, args.operation_id, confirmed=args.confirm_paid_call,
                max_cost_usd=args.max_cost_usd, max_output_tokens=args.max_output_tokens)
        else:
            service.tasks.get_draft(args.task_id)
            result = evidence.history(args.task_id)
            result["orphan_snapshot_images"] = evidence.orphan_snapshot_images()
        output = model_run_report(result) if args.command in {"run-real", "run-c02", "inspect-model-run"} else result
        print(json.dumps(output, ensure_ascii=False, indent=2))
        failed = ((args.command == "run" and result["run"]["status"] != "SUCCEEDED")
                  or (args.command in {"run-real", "run-c02"} and result["status"] != "SUCCEEDED"))
        return 1 if failed else 0
    except CredentialConfigError as exc:
        print(json.dumps({"error": {"code": "CREDENTIAL_CONFIG_INVALID", "stage": "ACCESS",
                                   "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
    except C01Error as exc:
        print(json.dumps({"error": exc.record()}, ensure_ascii=False), file=sys.stderr)
    except TaskNotFound as exc:
        print(json.dumps({"error": {"code": "TASK_NOT_FOUND", "stage": "EVIDENCE_QUERY",
                                   "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": {"code": "C01_STORAGE_OR_RECORD_ERROR",
                                   "stage": "DEVELOPER_ENTRY",
                                   "message": "存储或记录不可用；未声明成功。"},
                          "exception_type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
    return 2


def model_run_report(record: dict) -> dict:
    """Terminal-safe summary. Full request/images/raw responses remain in persisted Evidence."""
    keys = ("label", "transport_label", "run_id", "attempt_id", "snapshot_id", "requested_model",
            "capability", "task_id", "slot_id", "reference_asset_id",
            "status", "attempt_status", "dispatch_state", "provider_receipt", "model_api_call_attempted",
            "duration_ms", "provider_usage", "provider_cost", "usage_based_cost_estimate")
    result = {key: record[key] for key in keys if key in record}
    if record.get("error"):
        result["error"] = {key: record["error"][key] for key in ("code", "stage", "cause_type")
                           if key in record["error"]}
    result["full_evidence"] = "PERSISTED_IN_LOCAL_DATABASE; request/image/raw response omitted from terminal"
    return result


if __name__ == "__main__":
    raise SystemExit(main())
