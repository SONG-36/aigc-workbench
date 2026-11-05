"""Explicit human version reviews and the current legal delivery, separate from model Evidence."""

import json
from uuid import UUID, uuid4

from .application import TaskNotFound
from .c01_context import canonical, fingerprint, now
from .domain import Conflict, InvalidTask


class ReviewStore:
    def __init__(self, database, candidates, *, test_mode=False):
        self.database, self.candidates, self.test_mode = database, candidates, test_mode

    @staticmethod
    def _decode(row):
        body = json.loads(row["body_json"])
        if fingerprint(body) != row["body_sha256"] or any(str(body[key]) != str(row[key]) for key in
            ("id", "operation_id", "task_id", "slot_id", "image_version_id", "decision", "reviewer",
             "reviewed_at", "feedback", "image_sha256", "source")):
            raise InvalidTask("审核记录身份或完整性不一致。")
        return body

    def list(self, task_id, slot_id):
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM image_reviews WHERE task_id=? AND slot_id=? ORDER BY rowid",
                              (str(task_id), str(slot_id))).fetchall()
        return [self._decode(row) for row in rows]

    def current(self, task_id, slot_id):
        with self.database.connection() as db:
            pointer = db.execute("SELECT * FROM slot_deliveries WHERE task_id=? AND slot_id=?",
                                 (str(task_id), str(slot_id))).fetchone()
            if not pointer:
                return None
            review = db.execute("SELECT * FROM image_reviews WHERE id=?", (pointer["review_id"],)).fetchone()
        if not review:
            raise InvalidTask("有效交付缺少审核记录。")
        body = self._decode(review)
        if (body["decision"] != "PASS" or body["image_version_id"] != pointer["image_version_id"]
                or body["task_id"] != str(task_id) or body["slot_id"] != str(slot_id)
                or (not self.test_mode and body["source"] != "EXPLICIT_OPERATOR")):
            raise InvalidTask("当前交付不是对应图位的合法人工Pass。")
        return body

    def submit(self, task_id, slot_id, version_id, operation_id, decision, reviewer, feedback,
               expected_current_review_id):
        reviewer, feedback = reviewer.strip(), feedback.strip()
        if not reviewer or len(reviewer) > 80 or len(feedback) > 2000 or decision not in {"PASS", "REJECT"}:
            raise InvalidTask("请填写审核人；审核反馈或决定不符合要求。")
        candidate, _ = self.candidates.read_for_slot(task_id, slot_id, version_id)
        source = "TEST_REVIEW" if self.test_mode else "EXPLICIT_OPERATOR"
        intent = {"task_id": str(task_id), "slot_id": str(slot_id), "image_version_id": str(version_id),
                  "decision": decision, "reviewer": reviewer, "feedback": feedback, "source": source,
                  "expected_current_review_id": str(expected_current_review_id) if expected_current_review_id else None}
        with self.database.connection(write=True) as db:
            old = db.execute("SELECT * FROM image_reviews WHERE operation_id=?", (str(operation_id),)).fetchone()
            if old:
                body = self._decode(old)
                if any(body[key] != value for key, value in intent.items()):
                    raise Conflict("同一审核操作不能改变版本、反馈或审核决定。")
                return body
            current = db.execute("SELECT review_id FROM slot_deliveries WHERE task_id=? AND slot_id=?",
                                 (str(task_id), str(slot_id))).fetchone()
            current_id = current["review_id"] if current else None
            if current_id != intent["expected_current_review_id"]:
                raise Conflict("有效交付已被更新，请刷新并重新核对审核目标。")
            if decision == "REJECT" and db.execute("SELECT id FROM image_reviews WHERE image_version_id=? "
                                                   "AND decision='PASS'", (str(version_id),)).fetchone():
                raise Conflict("已Pass版本保留历史通过记录；本轮不提供撤销Pass。请审核新的候选。")
            body = {**intent, "id": str(uuid4()), "operation_id": str(operation_id), "reviewed_at": now(),
                    "image_sha256": candidate["image"]["sha256"]}
            db.execute("INSERT INTO image_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                body["id"], str(operation_id), str(task_id), str(slot_id), str(version_id), decision,
                reviewer, body["reviewed_at"], feedback, body["image_sha256"], source, canonical(body), fingerprint(body)))
            if decision == "PASS":
                db.execute("INSERT INTO slot_deliveries VALUES (?,?,?,?,?) ON CONFLICT(slot_id) DO UPDATE SET "
                           "image_version_id=excluded.image_version_id,review_id=excluded.review_id,updated_at=excluded.updated_at",
                           (str(task_id), str(slot_id), str(version_id), body["id"], body["reviewed_at"]))
        return body

    def delivery(self, task_id, slot_id, expected_review_id):
        review = self.current(task_id, slot_id)
        if not review:
            raise TaskNotFound("本图位尚无人工Pass的有效交付。")
        if review["id"] != str(expected_review_id):
            raise Conflict("有效交付已更新，请刷新后下载。")
        candidate, content = self.candidates.read_for_slot(task_id, slot_id, UUID(review["image_version_id"]))
        if candidate["image"]["sha256"] != review["image_sha256"]:
            raise InvalidTask("交付文件与人工Pass时的图片身份不一致。")
        return candidate, content
