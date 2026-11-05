"""Opt-in local developer entry. No developer HTTP routes or real-model switch."""

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
from .config import Settings
from .file_storage import LocalImageFiles
from .persistence import SQLiteTaskStore


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="C01 input preparation and TEST_ONLY execution; no AI calls.")
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
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        settings = Settings.from_environment()
        require_development(settings)  # Check before DB access, migration or directory creation.
        if not settings.database_path.is_file():
            raise C01Error("BUSINESS_DATABASE_MISSING", "SOURCE_SELECTION", "需要现有 S01 数据库。")
        database = SQLiteTaskStore(settings.database_path)
        if args.command in {"prepare", "run"}:
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
        else:
            service.tasks.get_draft(args.task_id)
            result = evidence.history(args.task_id)
            result["orphan_snapshot_images"] = evidence.orphan_snapshot_images()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if args.command == "run" and result["run"]["status"] != "SUCCEEDED" else 0
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


if __name__ == "__main__":
    raise SystemExit(main())
