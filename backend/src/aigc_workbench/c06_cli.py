"""Development-only C06 entry point; terminal summaries never include image Base64 or credentials."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
from uuid import UUID

from .application import TaskService
from .c01_cli import model_run_report
from .c01_context import C01Error
from .c01_execution import C01Service, require_development
from .c01_persistence import C01EvidenceStore
from .c06_candidates import CandidateStore
from .c06_generation import C06Service
from .config import CredentialConfigError, Settings, load_openai_credentials
from .file_storage import LocalImageFiles
from .persistence import SQLiteTaskStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--decision-snapshot-id", type=UUID, required=True)
    prepare.add_argument("--handoff-directory", type=Path, required=True)
    prepare.add_argument("--model", required=True)
    for name in ("preflight", "run"):
        command = commands.add_parser(name)
        command.add_argument("--snapshot-id", type=UUID, required=True)
        command.add_argument("--model", required=True)
        if name == "run":
            command.add_argument("--operation-id", type=UUID, required=True)
            command.add_argument("--confirm-paid-call", action="store_true")
            command.add_argument("--max-cost-usd", required=True)
    inspect = commands.add_parser("inspect-candidate")
    inspect.add_argument("--image-version-id", type=UUID, required=True)
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_environment()
        require_development(settings)
        if not settings.database_path.is_file():
            raise C01Error("BUSINESS_DATABASE_MISSING", "ACCESS", "需要现有业务数据库。")
        if args.command in {"preflight", "run"}:
            load_openai_credentials()
        database = SQLiteTaskStore(settings.database_path)
        if args.command == "prepare":
            database.initialize()
        base = C01Service(settings, TaskService(database, LocalImageFiles(settings.data_dir)),
                          C01EvidenceStore(database, settings.data_dir))
        service = C06Service(settings, base)
        if args.command == "prepare":
            snapshot = service.prepare(args.decision_snapshot_id, args.handoff_directory, args.model)
            result = {"snapshot_id": snapshot["id"], "capability": "C06", "network_requests": 0}
        elif args.command == "preflight":
            result = deepcopy(service.preflight(args.snapshot_id, args.model))
            for image in result["request"]["images"]:
                prefix, encoded = image["image_url"].split(",", 1)
                image["image_url"] = prefix + ",<omitted " + str(len(encoded)) + " base64 characters>"
            result["snapshot"] = {key: result["snapshot"][key] for key in
                                  ("id", "task_id", "slot_id", "decision_snapshot_id", "context_sha256")}
        elif args.command == "run":
            record = service.run(args.snapshot_id, args.model, args.operation_id,
                                 confirmed=args.confirm_paid_call, max_cost_usd=args.max_cost_usd)
            result = model_run_report(record)
            if "candidate" in record:
                result["candidate"] = record["candidate"]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if record["status"] == "SUCCEEDED" and "candidate" in record else 1
        else:
            result = CandidateStore(database, settings.data_dir).inspect(args.image_version_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except C01Error as exc:
        error = exc.record()
    except (CredentialConfigError, sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        error = {"code": "C06_ENTRY_OR_STORAGE_ERROR", "stage": "C06_ENTRY",
                 "message": "配置、来源或本地保存失败；先核对现有Run，禁止重新付费。"}
    print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
