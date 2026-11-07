"""SQLite persistence with immutable evaluation snapshots and atomic reviews."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .evaluator.validation import validate_draft
from .models import ChecklistVersion, Conversation, CriterionResult, EvaluationDraft
from .scoring import calculate_score, normalize_answer


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def gross_error_flag(evaluation: dict) -> bool | None:
    """Distinguish a confirmed flag from a check still awaiting evidence."""
    if evaluation["score"].get("gross_error", False):
        return True
    unresolved = {result["criterion_id"] for result in evaluation["results"] if result["status"] == "needs_review"}
    if any(criterion.get("gross_error_rule") and criterion["criterion_id"] in unresolved
           for criterion in evaluation["checklist"]["criteria"]):
        return None
    return False


class Store:
    def __init__(self, database_path: str | Path):
        self.memory = str(database_path) == ":memory:"
        self.path = f"file:cqa-{uuid4().hex}?mode=memory&cache=shared" if self.memory else str(Path(database_path).resolve())
        self.keeper = None

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15, uri=self.memory)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self, seed: ChecklistVersion, *, recover_jobs: bool = True):
        if self.memory:
            self.keeper = self.connect()
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.read() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    group_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    language TEXT NOT NULL, channel TEXT NOT NULL,
                    content_hash TEXT NOT NULL, payload TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS checklist_versions (
                    checklist_id TEXT NOT NULL, version INTEGER NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (checklist_id, version)
                );
                CREATE TABLE IF NOT EXISTS evaluation_jobs (
                    job_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE,
                    request_hash TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, error TEXT, evaluation_id TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    evaluation_id TEXT PRIMARY KEY, group_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS evaluations_conversation
                    ON evaluations(group_id, conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS appeals (
                    appeal_id TEXT PRIMARY KEY,
                    evaluation_id TEXT NOT NULL REFERENCES evaluations(evaluation_id),
                    criterion_id TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    scope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL, response TEXT NOT NULL,
                    PRIMARY KEY(scope, idempotency_key)
                );
            """)
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO checklist_versions VALUES(?,?,?,?)", (seed.checklist_id, seed.version, dumps(seed.model_dump()), now()))
            if recover_jobs:
                db.execute("UPDATE evaluation_jobs SET status='failed', error=?, updated_at=? WHERE status IN ('queued','running')", ("Сервер перезапущен до завершения оценки. Запустите новую проверку.", now()))

    def close(self):
        if self.keeper is not None:
            self.keeper.close()
            self.keeper = None

    def import_conversations(self, conversations: list[Conversation], group_id: str) -> dict:
        identities = {conversation.conversation_id for conversation in conversations}
        if any(conversation.group_id != group_id for conversation in conversations):
            raise ValueError("Imported conversations must belong to the selected group")
        if len(identities) != len(conversations):
            raise ValueError("Duplicate conversation_id inside the imported group")
        counts = {"group_id": group_id, "imported": 0, "updated": 0, "unchanged": 0, "total": len(conversations)}
        with self.transaction() as db:
            for conversation in conversations:
                payload = conversation.model_dump()
                content_hash = digest(payload)
                existing = db.execute("SELECT content_hash FROM conversations WHERE group_id=? AND conversation_id=?", (conversation.group_id, conversation.conversation_id)).fetchone()
                if existing and existing["content_hash"] == content_hash:
                    counts["unchanged"] += 1
                    continue
                counts["updated" if existing else "imported"] += 1
                db.execute("""INSERT INTO conversations VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(group_id,conversation_id) DO UPDATE SET language=excluded.language,
                    channel=excluded.channel,content_hash=excluded.content_hash,payload=excluded.payload,imported_at=excluded.imported_at""",
                    (conversation.group_id, conversation.conversation_id, conversation.language, conversation.channel, content_hash, dumps(payload), now()))
            counts["current_group_total"] = db.execute(
                "SELECT COUNT(*) FROM conversations WHERE group_id=?", (group_id,)
            ).fetchone()[0]
            counts["retained_from_previous_import"] = counts["current_group_total"] - len(identities)
        return counts

    def list_conversations(self, group_id=None, language=None, channel=None, gross_error=None):
        """Filter the latest reviewed result only when its source is still current.

        Unassessed or changed conversations and unresolved gross-error checks
        have an unknown flag (None) unless a violation has been confirmed.
        """
        conditions, params = [], []
        for name, value in (("group_id", group_id), ("language", language), ("channel", channel)):
            if value is not None:
                conditions.append(f"c.{name}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.read() as db:
            rows = db.execute("SELECT c.* FROM conversations c" + where + " ORDER BY c.group_id,c.conversation_id", params).fetchall()
            summaries = []
            for row in rows:
                conversation = json.loads(row["payload"])
                candidates = db.execute("SELECT payload FROM evaluations WHERE group_id=? AND conversation_id=? ORDER BY created_at DESC,rowid DESC", (row["group_id"], row["conversation_id"]))
                latest_payload = None
                # A slow old-snapshot job may finish after the current snapshot's
                # evaluation. Select the newest matching source, not just newest job.
                for candidate in candidates:
                    payload = json.loads(candidate["payload"])
                    if digest(payload["conversation"]) == row["content_hash"]:
                        latest_payload = payload
                        break
                latest_gross_error = gross_error_flag(latest_payload) if latest_payload else None
                if gross_error is not None and latest_gross_error is not gross_error:
                    continue
                messages = conversation["transcript"]
                if row["channel"] == "voice call":
                    preview = next((m["text"].strip() for speaker in ("client", "operator")
                                    for m in messages if m["speaker"] == speaker
                                    and any(char.isalpha() for char in m["text"])), "")
                else:
                    preview = next((m["text"] for m in messages if m["speaker"] == "client" and m["text"].strip()), messages[0]["text"])
                summaries.append({
                    "conversation_id": row["conversation_id"], "group_id": row["group_id"],
                    "channel": row["channel"], "language": row["language"],
                    "message_count": len(messages), "preview": preview[:200],
                    "latest_evaluation_id": latest_payload["evaluation_id"] if latest_payload else None,
                    "latest_score": latest_payload["score"]["total"] if latest_payload else None,
                    "latest_status": latest_payload["score"]["status"] if latest_payload else None,
                    "latest_gross_error": latest_gross_error,
                })
        return summaries

    def conversation(self, group_id: str, conversation_id: str) -> Conversation:
        with self.read() as db:
            row = db.execute("SELECT payload FROM conversations WHERE group_id=? AND conversation_id=?", (group_id, conversation_id)).fetchone()
        if row is None:
            raise NotFoundError("Диалог не найден в выбранном наборе")
        return Conversation.model_validate_json(row["payload"])

    def list_checklists(self):
        with self.read() as db:
            return [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM checklist_versions ORDER BY created_at DESC,version DESC")]

    def checklist(self, checklist_id: str, version: int) -> ChecklistVersion:
        with self.read() as db:
            row = db.execute("SELECT payload FROM checklist_versions WHERE checklist_id=? AND version=?", (checklist_id, version)).fetchone()
        if row is None:
            raise NotFoundError("Версия чек-листа не найдена")
        return ChecklistVersion.model_validate_json(row["payload"])

    def create_checklist(self, checklist: ChecklistVersion):
        checklist = checklist.model_copy(update={"version": 1})
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM checklist_versions WHERE checklist_id=?", (checklist.checklist_id,)).fetchone():
                raise ConflictError("Чек-лист с таким ID уже существует. Сохраните новую версию.")
            db.execute("INSERT INTO checklist_versions VALUES(?,?,?,?)", (checklist.checklist_id, 1, dumps(checklist.model_dump()), now()))
        return checklist.model_dump()

    def new_checklist_version(self, checklist_id, configuration):
        with self.transaction() as db:
            row = db.execute("SELECT MAX(version) AS version FROM checklist_versions WHERE checklist_id=?", (checklist_id,)).fetchone()
            if row["version"] is None:
                raise NotFoundError("Чек-лист не найден")
            checklist = ChecklistVersion.model_validate({**configuration, "checklist_id": checklist_id, "version": row["version"] + 1})
            db.execute("INSERT INTO checklist_versions VALUES(?,?,?,?)", (checklist_id, checklist.version, dumps(checklist.model_dump()), now()))
        return checklist.model_dump()

    @staticmethod
    def _public_job(row):
        result = {key: row[key] for key in ("job_id", "status", "error", "evaluation_id")}
        failure = json.loads(row["payload"]).get("failure")
        if failure is not None:
            result.update(error_code=failure["error_code"], model_info=failure["model_info"])
        return result

    def create_job(self, conversation: Conversation, checklist: ChecklistVersion, idempotency_key=None):
        payload = {"conversation": conversation.model_dump(), "checklist": checklist.model_dump()}
        request_hash = digest(payload)
        with self.transaction() as db:
            if idempotency_key:
                row = db.execute("SELECT * FROM evaluation_jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if row:
                    if row["request_hash"] != request_hash:
                        raise ConflictError("Этот ключ запуска уже использован для других данных")
                    return self._public_job(row), False
            job_id = uuid4().hex
            timestamp = now()
            db.execute("INSERT INTO evaluation_jobs VALUES(?,?,?,?,?,?,?,?,?)", (job_id, idempotency_key, request_hash, dumps(payload), "queued", None, None, timestamp, timestamp))
        return {"job_id": job_id, "status": "queued", "error": None, "evaluation_id": None}, True

    def job(self, job_id):
        with self.read() as db:
            row = db.execute("SELECT * FROM evaluation_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFoundError("Задача не найдена")
        return self._public_job(row)

    def begin_job(self, job_id):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM evaluation_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "queued":
                return None
            db.execute("UPDATE evaluation_jobs SET status='running',updated_at=? WHERE job_id=?", (now(), job_id))
            return json.loads(row["payload"])

    def fail_job(self, job_id, error, *, error_code=None, model_info=None):
        with self.transaction() as db:
            row = db.execute("SELECT payload,status FROM evaluation_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] not in {"queued", "running"}:
                return
            payload = json.loads(row["payload"])
            if error_code is not None or model_info is not None:
                # Evaluator diagnostics contain only validated counters and safe codes.
                # Store them beside the immutable request without changing its hash.
                payload["failure"] = {"error_code": error_code, "model_info": model_info}
            db.execute("UPDATE evaluation_jobs SET status='failed',error=?,payload=?,updated_at=? WHERE job_id=?", (error, dumps(payload), now(), job_id))

    def complete_job(self, job_id: str, conversation: Conversation, checklist: ChecklistVersion, draft: EvaluationDraft, model_info: dict):
        validate_draft(conversation, checklist, draft)
        score = calculate_score(checklist, draft.results)
        evaluation_id, timestamp = uuid4().hex, now()
        results = [result.model_dump() for result in draft.results]
        payload = {
            "evaluation_id": evaluation_id, "conversation": conversation.model_dump(),
            "checklist": checklist.model_dump(), "original_results": results, "results": results,
            "original_score": score, "score": score, "model_info": {**model_info, "prompt_version": draft.prompt_version},
            "revision": 1, "created_at": timestamp,
        }
        with self.transaction() as db:
            row = db.execute("SELECT status FROM evaluation_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "running":
                raise ConflictError("Задача уже завершена или прервана")
            db.execute("INSERT INTO evaluations VALUES(?,?,?,?,?,?)", (evaluation_id, conversation.group_id, conversation.conversation_id, 1, dumps(payload), timestamp))
            db.execute("UPDATE evaluation_jobs SET status='completed',evaluation_id=?,updated_at=? WHERE job_id=?", (evaluation_id, timestamp, job_id))
        return evaluation_id

    @staticmethod
    def _evaluation(db, evaluation_id):
        row = db.execute("SELECT payload FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
        if row is None:
            raise NotFoundError("Оценка не найдена")
        result = json.loads(row["payload"])
        result["appeals"] = [json.loads(appeal["payload"]) for appeal in db.execute("SELECT payload FROM appeals WHERE evaluation_id=? ORDER BY created_at", (evaluation_id,))]
        return result

    def evaluation(self, evaluation_id):
        with self.read() as db:
            return self._evaluation(db, evaluation_id)

    def list_evaluations(self, group_id=None, conversation_id=None, gross_error=None):
        """Filter each historical evaluation's current score, including appeals."""
        conditions, params = [], []
        for name, value in (("group_id", group_id), ("conversation_id", conversation_id)):
            if value is not None:
                conditions.append(f"{name}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.read() as db:
            evaluations = [self._evaluation(db, row["evaluation_id"]) for row in db.execute("SELECT evaluation_id FROM evaluations" + where + " ORDER BY created_at DESC,rowid DESC", params).fetchall()]
        return [evaluation for evaluation in evaluations if gross_error is None or gross_error_flag(evaluation) is gross_error]

    def submission_snapshot(self, group_id: str) -> tuple[list[Conversation], list[dict]]:
        """Read the source set and its evaluation history at the same point in time."""
        with self.read() as db:
            db.execute("BEGIN")
            conversations = [
                Conversation.model_validate_json(row["payload"])
                for row in db.execute(
                    "SELECT payload FROM conversations WHERE group_id=? ORDER BY conversation_id",
                    (group_id,),
                )
            ]
            evaluations = [
                self._evaluation(db, row["evaluation_id"])
                for row in db.execute(
                    "SELECT evaluation_id FROM evaluations WHERE group_id=? ORDER BY created_at DESC,rowid DESC",
                    (group_id,),
                )
            ]
        return conversations, evaluations

    def quality_summary(self, group_id: str, checklist: ChecklistVersion) -> dict:
        conversations, history = self.submission_snapshot(group_id)
        sources = {conversation.conversation_id: conversation.model_dump() for conversation in conversations}
        rules = checklist.model_dump()
        latest = {}
        for evaluation in history:
            source = evaluation["conversation"]
            identifier = source["conversation_id"]
            if evaluation["checklist"] == rules and source == sources.get(identifier):
                latest.setdefault(identifier, evaluation)

        weighted = {c.criterion_id: c for c in checklist.criteria if c.weight > 0}
        criteria = {key: {"criterion_id": key, "title": c.title, "applicable": 0, "dialogs": []}
                    for key, c in weighted.items()}
        critical = []
        affected = set()
        complete = incomplete = critical_unknown = 0
        for identifier, evaluation in latest.items():
            score = evaluation["score"]
            complete += score["status"] == "complete"
            incomplete += score["status"] == "incomplete"
            ref = {"conversation_id": identifier, "evaluation_id": evaluation["evaluation_id"]}
            for result in evaluation["results"]:
                criterion_id = result["criterion_id"]
                if criterion_id not in criteria or result["status"] != "applicable":
                    continue
                entry = criteria[criterion_id]
                entry["applicable"] += 1
                if normalize_answer(weighted[criterion_id], result["answer"]) < 1:
                    entry["dialogs"].append({**ref, "criterion_id": criterion_id})
                    affected.add(identifier)
            flag = gross_error_flag(evaluation)
            if flag:
                result = next(row for row in evaluation["results"] if row["gross_error"])
                critical.append({**ref, "criterion_id": result["criterion_id"]})
                affected.add(identifier)
            critical_unknown += flag is None

        violations = [entry for entry in criteria.values() if entry["dialogs"]]
        violations.sort(key=lambda entry: (-len(entry["dialogs"]), entry["title"].casefold()))
        return {"total": len(conversations), "complete": complete, "incomplete": incomplete,
                "unrated": len(conversations) - len(latest), "without_points": len(latest) - complete - incomplete,
                "affected": len(affected), "violations": violations, "critical": critical,
                "critical_unknown": critical_unknown}

    @staticmethod
    def _replay(db, scope, key, request_hash):
        if not key:
            return None
        row = db.execute("SELECT * FROM operations WHERE scope=? AND idempotency_key=?", (scope, key)).fetchone()
        if not row:
            return None
        if row["request_hash"] != request_hash:
            raise ConflictError("Этот ключ операции уже использован с другим содержимым")
        return json.loads(row["response"])

    @staticmethod
    def _record_operation(db, scope, key, request_hash, response):
        if key:
            db.execute("INSERT INTO operations VALUES(?,?,?,?)", (scope, key, request_hash, dumps(response)))

    def create_appeal(self, evaluation_id, criterion_id, comment, idempotency_key=None):
        request_hash = digest({"evaluation_id": evaluation_id, "criterion_id": criterion_id, "comment": comment})
        with self.transaction() as db:
            replay = self._replay(db, "appeal", idempotency_key, request_hash)
            if replay is not None:
                return replay
            evaluation = self._evaluation(db, evaluation_id)
            if criterion_id not in {r["criterion_id"] for r in evaluation["results"]}:
                raise NotFoundError("Критерий отсутствует в этой оценке")
            if db.execute("SELECT 1 FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status='open'", (evaluation_id, criterion_id)).fetchone():
                raise ConflictError("Для этого критерия уже открыта апелляция")
            appeal = {"appeal_id": uuid4().hex, "evaluation_id": evaluation_id, "criterion_id": criterion_id, "comment": comment, "status": "open", "created_at": now(), "decision": None}
            db.execute("INSERT INTO appeals VALUES(?,?,?,?,?,?)", (appeal["appeal_id"], evaluation_id, criterion_id, "open", dumps(appeal), appeal["created_at"]))
            self._record_operation(db, "appeal", idempotency_key, request_hash, appeal)
        return appeal

    def decide_appeal(self, appeal_id, action, reason, replacement_result, expected_revision, idempotency_key=None):
        replacement = replacement_result.model_dump() if replacement_result else None
        request_hash = digest({"appeal_id": appeal_id, "action": action, "reason": reason, "replacement_result": replacement, "expected_revision": expected_revision})
        with self.transaction() as db:
            replay = self._replay(db, "decision", idempotency_key, request_hash)
            if replay is not None:
                return replay
            row = db.execute("SELECT payload FROM appeals WHERE appeal_id=?", (appeal_id,)).fetchone()
            if row is None:
                raise NotFoundError("Апелляция не найдена")
            appeal = json.loads(row["payload"])
            evaluation = self._evaluation(db, appeal["evaluation_id"])
            if appeal["status"] != "open":
                raise ConflictError("Апелляция уже рассмотрена")
            if evaluation["revision"] != expected_revision:
                raise ConflictError("Оценка уже изменена. Обновите результат перед решением.")
            if action == "replace":
                if replacement_result is None or replacement_result.criterion_id != appeal["criterion_id"]:
                    raise ValueError("Замена должна относиться к критерию апелляции")
                evaluation["results"] = [replacement if result["criterion_id"] == appeal["criterion_id"] else result for result in evaluation["results"]]
            conversation = Conversation.model_validate(evaluation["conversation"])
            checklist = ChecklistVersion.model_validate(evaluation["checklist"])
            draft = EvaluationDraft(
                conversation_id=conversation.conversation_id, group_id=conversation.group_id,
                checklist_id=checklist.checklist_id, checklist_version=checklist.version,
                prompt_version=evaluation["model_info"].get("prompt_version", "review"),
                results=[CriterionResult.model_validate(result) for result in evaluation["results"]],
            )
            validate_draft(conversation, checklist, draft)
            evaluation["score"] = calculate_score(checklist, draft.results)
            evaluation["revision"] += 1
            timestamp = now()
            appeal["status"] = "resolved"
            appeal["decision"] = {"action": action, "reason": reason, "replacement_result": replacement, "created_at": timestamp}
            db.execute("UPDATE appeals SET status='resolved',payload=? WHERE appeal_id=?", (dumps(appeal), appeal_id))
            payload = {key: value for key, value in evaluation.items() if key != "appeals"}
            db.execute("UPDATE evaluations SET revision=?,payload=? WHERE evaluation_id=?", (evaluation["revision"], dumps(payload), appeal["evaluation_id"]))
            result = self._evaluation(db, appeal["evaluation_id"])
            self._record_operation(db, "decision", idempotency_key, request_hash, result)
        return result
