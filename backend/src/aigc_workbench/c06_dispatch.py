"""Bounded local C06/C07 workers; durable per-slot receipts and zero automatic replay."""

from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
import sqlite3
from uuid import UUID

from .c01_context import C01Error, canonical, now
from .c01_model_store import C01ModelStore
from .domain import Conflict

ACTIVE = {"QUEUED", "RUNNING", "OUTCOME_UNKNOWN"}


class C06Dispatcher:
    def __init__(self, database, data_dir, execute, mode="REAL", max_workers=3):
        self.database, self.execute, self.mode = database, execute, mode
        self.lock_path = data_dir / "c06-dispatch.lock"
        self.lock = None
        self.pool = None
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise ValueError('generation max_workers must be between 1 and 16')
        self.max_workers = max_workers

    def start(self):
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        self.lock = os.fdopen(descriptor, "r+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close(); self.lock = None
            return  # Other process may serve reads, but cannot dispatch or recover its worker.
        with self.database.connection() as db:
            interrupted = db.execute("SELECT * FROM c06_dispatches WHERE state IN ('QUEUED','RUNNING')").fetchall()
        for receipt in interrupted:
            self._finish(dict(receipt), interrupted=True)
        self.pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="c06-local")

    def close(self):
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=False)
        if self.lock:
            self.lock.close(); self.lock = None

    def find(self, operation_id):
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM c06_dispatches WHERE operation_id=?", (str(operation_id),)).fetchone()
        return dict(row) if row else None

    def replay(self, operation_id, task_id, slot_id, quote_token, budget=None):
        old = self.find(operation_id)
        if old and any(old[key] != str(value) for key, value in {
                "task_id": task_id, "slot_id": slot_id, "quote_token": quote_token,
                **({"max_cost_usd": budget} if budget is not None else {})}.items()):
            raise Conflict("同一生图操作不能改变图位、制作依据或执行策略。")
        return old

    def submit(self, operation_id, task_id, slot_id, snapshot_id, quote_token, budget, *, initial_progress=None):
        if not self.pool:
            raise C01Error("GENERATION_WORKER_UNAVAILABLE", "DISPATCH", "生图执行进程不可用；未提交。")
        created = now()
        try:
            with self.database.connection(write=True) as db:
                old = db.execute("SELECT * FROM c06_dispatches WHERE operation_id=?", (str(operation_id),)).fetchone()
                if old:
                    return self.replay(operation_id, task_id, slot_id, quote_token, budget)
                # 删除可能在快照准备之后发生；在同一写事务内重新检查，避免误付费。
                task = db.execute('SELECT deleted_at FROM task_drafts WHERE id=?', (str(task_id),)).fetchone()
                if task is None or task['deleted_at'] is not None:
                    raise Conflict('任务已删除，未提交模型调用。')
                if db.execute("SELECT id FROM c01_model_runs WHERE operation_id=?", (str(operation_id),)).fetchone():
                    raise Conflict("操作身份已用于历史模型执行，不能重新提交为新生图。")
                db.execute("INSERT INTO c06_dispatches "
                           "(operation_id,task_id,slot_id,snapshot_id,quote_token,max_cost_usd,state,execution_mode,created_at,updated_at,error_json,progress_json) "
                           "VALUES (?,?,?,?,?,?,'QUEUED',?,?,?,NULL,?)",
                           tuple(map(str, (operation_id, task_id, slot_id, snapshot_id, quote_token, budget)))
                           + (self.mode, created, created, canonical(initial_progress or {})))
        except sqlite3.IntegrityError as exc:
            raise Conflict("本图位已有执行中或结果未知的操作；请先查询，不要重复付费。") from exc
        receipt = self.find(operation_id)
        try:
            self.pool.submit(self._work, receipt)
        except RuntimeError:
            self._set(receipt, "FAILED", {"code": "DISPATCH_NOT_STARTED", "message": "派发未启动，未调用模型。"})
        return self.find(operation_id)

    def _set(self, receipt, state, error=None):
        with self.database.connection(write=True) as db:
            db.execute("UPDATE c06_dispatches SET state=?,updated_at=?,error_json=? WHERE operation_id=?",
                       (state, now(), canonical(error) if error else None, receipt["operation_id"]))

    def progress(self, operation_id, stage, steps):
        with self.database.connection(write=True) as db:
            db.execute("UPDATE c06_dispatches SET progress_json=?,updated_at=? WHERE operation_id=? AND state='RUNNING'",
                       (canonical({"stage": stage, "steps": steps}), now(), str(operation_id)))

    def _work(self, receipt):
        with self.database.connection(write=True) as db:
            claimed = db.execute("UPDATE c06_dispatches SET state='RUNNING',updated_at=? "
                                 "WHERE operation_id=? AND state='QUEUED'", (now(), receipt["operation_id"])).rowcount
        if not claimed:
            return
        error = None
        try:
            self.execute(UUID(receipt["snapshot_id"]), UUID(receipt["operation_id"]), receipt["max_cost_usd"])
        except C01Error as exc:
            error = exc.record()
        except Exception:
            # Never expose credentials, request bytes, absolute paths or arbitrary exception text.
            error = {"code": "C06_EXECUTION_OR_SAVE_ERROR", "message": "执行或候选保存失败；查询已有记录，不自动重试。"}
        self._finish(receipt, error=error)

    def _finish(self, receipt, error=None, interrupted=False):
        with self.database.connection() as db:
            row = db.execute("SELECT id FROM c01_model_runs WHERE operation_id=?", (receipt["operation_id"],)).fetchone()
            candidate = db.execute("SELECT id FROM image_versions WHERE run_id=?", (row["id"],)).fetchone() if row else None
        if row:
            run = C01ModelStore(self.database).inspect(UUID(row["id"]))
            state = run["status"]
            if state == "SUCCEEDED" and not candidate:
                state, error = "FAILED", {"code": "CANDIDATE_NOT_SAVED", "message": "模型已返回，候选未可靠保存；保留Evidence，不重新付费。"}
            elif state == "RUNNING":
                state, error = "OUTCOME_UNKNOWN", {"code": "EXECUTION_INTERRUPTED", "message": "执行中断，远端结果或费用未知；禁止自动重试。"}
            else:
                error = run.get("error") or error
        elif error and error.get("outcome_unknown"):
            state = "OUTCOME_UNKNOWN"
        elif interrupted and receipt["state"] != "QUEUED":
            state, error = "OUTCOME_UNKNOWN", {"code": "EXECUTION_INTERRUPTED", "message": "派发过程已中断，先核对结果，不自动重试。"}
        else:
            state = "FAILED"
            error = error or {"code": "DISPATCH_NOT_STARTED", "message": "进程在派发前停止；未自动重新执行。"}
        self._set(receipt, state, error)
