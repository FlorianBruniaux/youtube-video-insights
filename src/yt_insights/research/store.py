"""SQLite-backed durable state for the cumulative research workflow."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
from datetime import UTC, date, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, TypeVar

from .models import (
    AcquisitionAttempt,
    CandidateStatus,
    CoverageMetrics,
    DatabaseSnapshot,
    DecisionRecord,
    EventRecord,
    FreshnessAssessment,
    FreshnessProfile,
    PassageEvidence,
    QuerySpec,
    RequiredUserAction,
    ResearchAcquisitionOutcome,
    ResearchAssessment,
    ResearchCandidate,
    ResearchSession,
    ResearchState,
    SessionHistory,
    VideoEvidence,
    normalize_research_text,
)


_SCHEMA_VERSION = 1
_ERROR_CODE = re.compile(r"[\x21-\x7e]{1,100}")
_T = TypeVar("_T")

_FAILURE_RETRY_TARGETS = {
    ResearchState.ASSESSING: ResearchState.ASSESSING,
    ResearchState.DISCOVERING: ResearchState.DISCOVERING,
    ResearchState.ACQUIRING: ResearchState.ACQUIRING,
    ResearchState.REINDEXING: ResearchState.REINDEXING,
}

_SCHEMA = (
    "CREATE TABLE schema_meta(version INTEGER NOT NULL)",
    """CREATE TABLE research_sessions(
        session_id TEXT PRIMARY KEY, topic TEXT NOT NULL,
        discovery_fingerprint TEXT NOT NULL, freshness_profile TEXT NOT NULL,
        state TEXT NOT NULL, required_user_action TEXT, revision INTEGER NOT NULL,
        retry_target TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        completed_at TEXT
    )""",
    """CREATE TABLE research_queries(
        session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL, query_text TEXT NOT NULL, normalized_query TEXT NOT NULL,
        PRIMARY KEY(session_id, ordinal), UNIQUE(session_id, normalized_query)
    )""",
    """CREATE TABLE research_languages(
        session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL, language TEXT NOT NULL,
        PRIMARY KEY(session_id, ordinal), UNIQUE(session_id, language)
    )""",
    """CREATE TABLE research_assessments(
        assessment_id INTEGER PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
        session_revision INTEGER NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(session_id, session_revision)
    )""",
    """CREATE TABLE research_candidates(
        session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
        snapshot_revision INTEGER NOT NULL, video_id TEXT NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(session_id, snapshot_revision, video_id)
    )""",
    """CREATE TABLE research_decisions(
        idempotency_key TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
        expected_revision INTEGER NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE research_acquisition_attempts(
        attempt_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
        expected_revision INTEGER NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE research_acquisition_outcomes(
        attempt_id TEXT NOT NULL REFERENCES research_acquisition_attempts(attempt_id) ON DELETE CASCADE,
        video_id TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT, source_sha256 TEXT,
        PRIMARY KEY(attempt_id, video_id)
    )""",
    """CREATE TABLE research_events(
        event_id INTEGER PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
        from_state TEXT, to_state TEXT NOT NULL, event_code TEXT NOT NULL,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
)

_SCHEMA_COLUMNS = {
    "schema_meta": (("version", "INTEGER", 1, 0),),
    "research_sessions": (("session_id", "TEXT", 0, 1), ("topic", "TEXT", 1, 0), ("discovery_fingerprint", "TEXT", 1, 0), ("freshness_profile", "TEXT", 1, 0), ("state", "TEXT", 1, 0), ("required_user_action", "TEXT", 0, 0), ("revision", "INTEGER", 1, 0), ("retry_target", "TEXT", 0, 0), ("created_at", "TEXT", 1, 0), ("updated_at", "TEXT", 1, 0), ("completed_at", "TEXT", 0, 0)),
    "research_queries": (("session_id", "TEXT", 1, 1), ("ordinal", "INTEGER", 1, 2), ("query_text", "TEXT", 1, 0), ("normalized_query", "TEXT", 1, 0)),
    "research_languages": (("session_id", "TEXT", 1, 1), ("ordinal", "INTEGER", 1, 2), ("language", "TEXT", 1, 0)),
    "research_assessments": (("assessment_id", "INTEGER", 0, 1), ("session_id", "TEXT", 1, 0), ("session_revision", "INTEGER", 1, 0), ("payload_json", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0)),
    "research_candidates": (("session_id", "TEXT", 1, 1), ("snapshot_revision", "INTEGER", 1, 2), ("video_id", "TEXT", 1, 3), ("payload_json", "TEXT", 1, 0), ("status", "TEXT", 1, 0), ("updated_at", "TEXT", 1, 0)),
    "research_decisions": (("idempotency_key", "TEXT", 0, 1), ("session_id", "TEXT", 1, 0), ("expected_revision", "INTEGER", 1, 0), ("action", "TEXT", 1, 0), ("payload_json", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0)),
    "research_acquisition_attempts": (("attempt_id", "TEXT", 0, 1), ("idempotency_key", "TEXT", 1, 0), ("session_id", "TEXT", 1, 0), ("expected_revision", "INTEGER", 1, 0), ("payload_json", "TEXT", 1, 0), ("status", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0), ("updated_at", "TEXT", 1, 0)),
    "research_acquisition_outcomes": (("attempt_id", "TEXT", 1, 1), ("video_id", "TEXT", 1, 2), ("status", "TEXT", 1, 0), ("error_code", "TEXT", 0, 0), ("source_sha256", "TEXT", 0, 0)),
    "research_events": (("event_id", "INTEGER", 0, 1), ("session_id", "TEXT", 1, 0), ("from_state", "TEXT", 0, 0), ("to_state", "TEXT", 1, 0), ("event_code", "TEXT", 1, 0), ("payload_json", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0)),
}

_SCHEMA_INDEXES = {
    "schema_meta": frozenset(),
    "research_sessions": frozenset({("pk", ("session_id",))}),
    "research_queries": frozenset({("pk", ("session_id", "ordinal")), ("u", ("session_id", "normalized_query"))}),
    "research_languages": frozenset({("pk", ("session_id", "ordinal")), ("u", ("session_id", "language"))}),
    "research_assessments": frozenset({("u", ("session_id", "session_revision"))}),
    "research_candidates": frozenset({("pk", ("session_id", "snapshot_revision", "video_id"))}),
    "research_decisions": frozenset({("pk", ("idempotency_key",))}),
    "research_acquisition_attempts": frozenset({("pk", ("attempt_id",)), ("u", ("idempotency_key",))}),
    "research_acquisition_outcomes": frozenset({("pk", ("attempt_id", "video_id"))}),
    "research_events": frozenset(),
}

_CASCADE_SESSION_FOREIGN_KEY_TABLES = frozenset({"research_queries", "research_languages", "research_assessments", "research_candidates", "research_decisions", "research_acquisition_attempts", "research_events"})


class ResearchStore:
    """Own a portable SQLite file and enforce workflow transitions atomically."""

    def __init__(self, database_path: str | Path, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._path = Path(database_path)
        if not self._path.is_absolute():
            raise ValueError("database path must be absolute")
        self._now = now
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._initialize()
        else:
            with self._open_unchecked() as connection:
                self._validate_schema(connection)
        stat = self._path.stat()
        self._identity = (stat.st_dev, stat.st_ino)

    def create_session(self, *, session_id: str, topic: str, queries: tuple[QuerySpec, ...], languages: tuple[str, ...], freshness_profile: FreshnessProfile, discovery_fingerprint: str) -> ResearchSession:
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            now = self._timestamp()
            try:
                connection.execute(
                    """INSERT INTO research_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (session_id, topic, discovery_fingerprint, freshness_profile.value,
                     ResearchState.ASSESSING.value, None, 0, None, now, now, None),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("session already exists") from exc
            for ordinal, query in enumerate(queries):
                connection.execute(
                    "INSERT INTO research_queries VALUES (?, ?, ?, ?)",
                    (session_id, ordinal, query.text, normalize_research_text(query.text)),
                )
            for ordinal, language in enumerate(languages):
                connection.execute(
                    "INSERT INTO research_languages VALUES (?, ?, ?)",
                    (session_id, ordinal, language),
                )
            self._event(connection, session_id, None, ResearchState.ASSESSING, "session_created", {})
            return self._session(connection, session_id)
        return self._write(operation)

    def get_session(self, session_id: str) -> ResearchSession:
        with self._connection() as connection:
            return self._session(connection, session_id)

    def record_assessment(self, session_id: str, *, expected_revision: int, assessment: ResearchAssessment) -> ResearchSession:
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            session = self._expected(connection, session_id, expected_revision, {ResearchState.ASSESSING})
            next_revision = session.revision + 1
            connection.execute(
                "INSERT INTO research_assessments(session_id, session_revision, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (session_id, next_revision, _canonical_json(assessment), _iso(assessment.created_at)),
            )
            return self._transition(connection, session, ResearchState.AWAITING_SUFFICIENCY, "assessment_recorded", {}, required_action=RequiredUserAction.CONFIRM_SUFFICIENCY_OR_REFRESH)
        return self._write(operation)

    def get_latest_assessment(self, session_id: str) -> ResearchAssessment | None:
        with self._connection() as connection:
            self._session(connection, session_id)
            row = connection.execute(
                "SELECT payload_json FROM research_assessments WHERE session_id = ? ORDER BY session_revision DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            return None if row is None else _assessment_from_json(row[0])

    def decide_sufficiency(self, session_id: str, *, expected_revision: int, sufficient: bool, idempotency_key: str) -> ResearchSession:
        payload = {"sufficient": sufficient}
        action = "sufficient" if sufficient else "refresh"
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            replayed = self._idempotent_decision(connection, idempotency_key, session_id, expected_revision, action, payload)
            if replayed is not None:
                return replayed
            session = self._expected(connection, session_id, expected_revision, {ResearchState.AWAITING_SUFFICIENCY})
            target = ResearchState.COMPLETED if sufficient else ResearchState.DISCOVERING
            result = self._transition(connection, session, target, action, payload)
            self._decision(connection, idempotency_key, session_id, expected_revision, action, payload, result)
            return result
        return self._write(operation)

    def last_successful_discovery_at(self, discovery_fingerprint: str) -> datetime | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT MAX(events.created_at) FROM research_events AS events
                JOIN research_sessions AS sessions ON sessions.session_id = events.session_id
                WHERE sessions.discovery_fingerprint = ? AND events.event_code = 'candidates_recorded'""",
                (discovery_fingerprint,),
            ).fetchone()
            return None if row is None or row[0] is None else _datetime(row[0])

    def record_candidates(self, session_id: str, *, expected_revision: int, candidates: tuple[ResearchCandidate, ...], provider_name: str, provider_version: int, errors: tuple[str, ...]) -> ResearchSession:
        if not isinstance(candidates, tuple) or not isinstance(errors, tuple):
            raise TypeError("candidates and errors must be tuples")
        if any(not _is_error_code(error) for error in errors):
            raise ValueError("error code must be 1 to 100 printable ASCII characters")
        if len({candidate.video_id for candidate in candidates}) != len(candidates):
            raise ValueError("candidates must not contain duplicate videos")
        payload = {"provider_name": provider_name, "provider_version": provider_version, "errors": list(errors)}
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            session = self._expected(connection, session_id, expected_revision, {ResearchState.DISCOVERING})
            snapshot_revision = session.revision + 1
            now = self._timestamp()
            for candidate in candidates:
                connection.execute(
                    "INSERT INTO research_candidates VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, snapshot_revision, candidate.video_id, _canonical_json(candidate), candidate.status.value, now),
                )
            return self._transition(connection, session, ResearchState.AWAITING_CANDIDATES, "candidates_recorded", payload, required_action=RequiredUserAction.APPROVE_CANDIDATES_OR_CANCEL)
        return self._write(operation)

    def list_candidates(self, session_id: str) -> tuple[ResearchCandidate, ...]:
        with self._connection() as connection:
            self._session(connection, session_id)
            rows = connection.execute(
                """SELECT payload_json FROM research_candidates WHERE session_id = ?
                AND snapshot_revision = (SELECT MAX(snapshot_revision) FROM research_candidates WHERE session_id = ?)
                ORDER BY video_id""",
                (session_id, session_id),
            ).fetchall()
            return tuple(_candidate_from_json(row[0]) for row in rows)

    def approve_candidates(self, session_id: str, *, expected_revision: int, video_ids: tuple[str, ...], idempotency_key: str) -> ResearchSession:
        if not isinstance(video_ids, tuple) or not video_ids or len(set(video_ids)) != len(video_ids):
            raise ValueError("video IDs must be a non-empty tuple without duplicates")
        payload = {"video_ids": list(video_ids)}
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            replayed = self._idempotent_decision(connection, idempotency_key, session_id, expected_revision, "approve_candidates", payload)
            if replayed is not None:
                return replayed
            session = self._expected(connection, session_id, expected_revision, {ResearchState.AWAITING_CANDIDATES})
            candidates = {candidate.video_id: candidate for candidate in self.list_candidates(session_id)}
            if any(video_id not in candidates for video_id in video_ids):
                raise ValueError("approved video is not a candidate")
            now = self._timestamp()
            for video_id in video_ids:
                approved = replace(candidates[video_id], status=CandidateStatus.APPROVED)
                connection.execute(
                    """UPDATE research_candidates SET payload_json = ?, status = ?, updated_at = ?
                    WHERE session_id = ? AND video_id = ? AND snapshot_revision = ?""",
                    (_canonical_json(approved), CandidateStatus.APPROVED.value, now, session_id, video_id, self._candidate_revision(connection, session_id)),
                )
            result = self._transition(connection, session, ResearchState.ACQUIRING, "candidates_approved", payload)
            self._decision(connection, idempotency_key, session_id, expected_revision, "approve_candidates", payload, result)
            return result
        return self._write(operation)

    def start_acquisition_attempt(self, session_id: str, *, expected_revision: int, video_ids: tuple[str, ...], idempotency_key: str, attempt_id: str) -> AcquisitionAttempt:
        if not isinstance(video_ids, tuple) or not video_ids or len(set(video_ids)) != len(video_ids):
            raise ValueError("video IDs must be a non-empty tuple without duplicates")
        payload = {"attempt_id": attempt_id, "video_ids": list(video_ids)}
        def operation(connection: sqlite3.Connection) -> AcquisitionAttempt:
            row = connection.execute("SELECT * FROM research_acquisition_attempts WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if row is not None:
                existing = self._attempt_from_row(row)
                if existing.session_id != session_id or existing.revision != expected_revision or existing.attempt_id != attempt_id or existing.video_ids != video_ids:
                    raise ValueError("idempotency key payload differs")
                return existing
            session = self._expected(connection, session_id, expected_revision, {ResearchState.ACQUIRING})
            now = self._timestamp()
            try:
                connection.execute(
                    "INSERT INTO research_acquisition_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (attempt_id, idempotency_key, session_id, expected_revision, _canonical_json(payload), "running", now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("attempt already exists") from exc
            self._event(connection, session_id, session.state, session.state, "acquisition_attempt_started", payload)
            return AcquisitionAttempt(attempt_id, idempotency_key, session_id, expected_revision, "running", video_ids, _datetime(now), _datetime(now))
        return self._write(operation)

    def record_acquisition_batch(self, session_id: str, *, expected_revision: int, attempt_id: str, outcomes: tuple[ResearchAcquisitionOutcome, ...]) -> ResearchSession:
        if not isinstance(outcomes, tuple) or not outcomes:
            raise ValueError("outcomes must be a non-empty tuple")
        if any(outcome.error_code is not None and not _is_error_code(outcome.error_code) for outcome in outcomes):
            raise ValueError("error code must be 1 to 100 printable ASCII characters")
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            session = self._expected(connection, session_id, expected_revision, {ResearchState.ACQUIRING})
            row = connection.execute("SELECT * FROM research_acquisition_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if row is None:
                raise ValueError("acquisition attempt does not exist")
            attempt = self._attempt_from_row(row)
            if attempt.session_id != session_id or attempt.revision != expected_revision or attempt.status != "running":
                raise ValueError("acquisition attempt is not active for this revision")
            if len({outcome.video_id for outcome in outcomes}) != len(outcomes) or any(outcome.attempt_id != attempt_id for outcome in outcomes):
                raise ValueError("outcomes must be unique and belong to the attempt")
            if {outcome.video_id for outcome in outcomes} != set(attempt.video_ids):
                raise ValueError("outcomes must cover exactly the reserved videos")
            now = self._timestamp()
            revision = self._candidate_revision(connection, session_id)
            candidates = {candidate.video_id: candidate for candidate in self.list_candidates(session_id)}
            for outcome in outcomes:
                if outcome.video_id not in candidates:
                    raise ValueError("outcome video is not a candidate")
                connection.execute(
                    "INSERT INTO research_acquisition_outcomes VALUES (?, ?, ?, ?, ?)",
                    (attempt_id, outcome.video_id, outcome.status.value, outcome.error_code, outcome.source_sha256),
                )
                updated = replace(candidates[outcome.video_id], status=outcome.status)
                connection.execute(
                    "UPDATE research_candidates SET payload_json = ?, status = ?, updated_at = ? WHERE session_id = ? AND snapshot_revision = ? AND video_id = ?",
                    (_canonical_json(updated), outcome.status.value, now, session_id, revision, outcome.video_id),
                )
            connection.execute("UPDATE research_acquisition_attempts SET status = 'completed', updated_at = ? WHERE attempt_id = ?", (now, attempt_id))
            return self._transition(connection, session, ResearchState.REINDEXING, "acquisition_batch_recorded", {"attempt_id": attempt_id})
        return self._write(operation)

    def complete_reindexing(self, session_id: str, *, expected_revision: int) -> ResearchSession:
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            session = self._expected(connection, session_id, expected_revision, {ResearchState.REINDEXING})
            return self._transition(connection, session, ResearchState.ASSESSING, "reindexing_completed", {})
        return self._write(operation)

    def get_session_history(self, session_id: str) -> SessionHistory:
        with self._connection() as connection:
            self._session(connection, session_id)
            assessments = tuple(_assessment_from_json(row[0]) for row in connection.execute("SELECT payload_json FROM research_assessments WHERE session_id = ? ORDER BY session_revision", (session_id,)))
            decisions = tuple(DecisionRecord(row[0], row[1], row[2], _datetime(row[3])) for row in connection.execute("SELECT idempotency_key, action, payload_json, created_at FROM research_decisions WHERE session_id = ? ORDER BY rowid", (session_id,)))
            attempts = tuple(self._attempt_from_row(row) for row in connection.execute("SELECT * FROM research_acquisition_attempts WHERE session_id = ? ORDER BY created_at, attempt_id", (session_id,)))
            outcomes = tuple(ResearchAcquisitionOutcome(row[0], row[1], CandidateStatus(row[2]), row[3], row[4]) for row in connection.execute("""SELECT outcomes.attempt_id, outcomes.video_id, outcomes.status, outcomes.error_code, outcomes.source_sha256 FROM research_acquisition_outcomes AS outcomes JOIN research_acquisition_attempts AS attempts ON attempts.attempt_id = outcomes.attempt_id WHERE attempts.session_id = ? ORDER BY attempts.created_at, outcomes.video_id""", (session_id,)))
            events = tuple(EventRecord(row[0], None if row[1] is None else ResearchState(row[1]), ResearchState(row[2]), row[3], row[4], _datetime(row[5])) for row in connection.execute("SELECT event_id, from_state, to_state, event_code, payload_json, created_at FROM research_events WHERE session_id = ? ORDER BY event_id", (session_id,)))
            return SessionHistory(assessments, decisions, attempts, outcomes, events)

    def record_failure(self, session_id: str, *, expected_revision: int, retry_target: ResearchState, error_code: str) -> ResearchSession:
        if not _is_error_code(error_code):
            raise ValueError("error code must be 1 to 100 printable ASCII characters")
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            session = self._expected(connection, session_id, expected_revision, set(_FAILURE_RETRY_TARGETS))
            if _FAILURE_RETRY_TARGETS[session.state] is not retry_target:
                raise ValueError("retry target must match the failed workflow state")
            return self._transition(connection, session, ResearchState.FAILED_RETRYABLE, "failure_recorded", {"error_code": error_code, "retry_target": retry_target.value}, retry_target=retry_target)
        return self._write(operation)

    def retry(self, session_id: str, *, expected_revision: int, idempotency_key: str) -> ResearchSession:
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            replayed = self._idempotent_decision(connection, idempotency_key, session_id, expected_revision, "retry", {})
            if replayed is not None:
                return replayed
            session = self._expected(connection, session_id, expected_revision, {ResearchState.FAILED_RETRYABLE})
            if session.retry_target not in set(_FAILURE_RETRY_TARGETS.values()):
                raise ValueError("failed session has no retry target")
            result = self._transition(connection, session, session.retry_target, "retry", {}, retry_target=None)
            self._decision(connection, idempotency_key, session_id, expected_revision, "retry", {}, result)
            return result
        return self._write(operation)

    def cancel(self, session_id: str, *, expected_revision: int, idempotency_key: str) -> ResearchSession:
        def operation(connection: sqlite3.Connection) -> ResearchSession:
            replayed = self._idempotent_decision(connection, idempotency_key, session_id, expected_revision, "cancel", {})
            if replayed is not None:
                return replayed
            session = self._expected(connection, session_id, expected_revision, {ResearchState.AWAITING_SUFFICIENCY, ResearchState.AWAITING_CANDIDATES})
            result = self._transition(connection, session, ResearchState.CANCELLED, "cancel", {})
            self._decision(connection, idempotency_key, session_id, expected_revision, "cancel", {}, result)
            return result
        return self._write(operation)

    def _initialize(self) -> None:
        with self._open_unchecked() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_meta(version) VALUES (?)", (_SCHEMA_VERSION,))
                self._validate_schema(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _open_unchecked(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _connection(self) -> sqlite3.Connection:
        self._assert_identity()
        return self._open_unchecked()

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = operation(connection)
                connection.commit()
                return result
            except BaseException:
                connection.rollback()
                raise

    def _assert_identity(self) -> None:
        stat = self._path.stat()
        if (stat.st_dev, stat.st_ino) != self._identity:
            raise RuntimeError("database identity changed; refusing replacement")

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        try:
            versions = [row[0] for row in connection.execute("SELECT version FROM schema_meta")]
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError as exc:
            raise ValueError("database schema is unsupported") from exc
        if versions != [_SCHEMA_VERSION] or tables != set(_SCHEMA_COLUMNS):
            raise ValueError("database schema is unsupported")
        for table, expected_columns in _SCHEMA_COLUMNS.items():
            columns = tuple((row[1], row[2], row[3], row[5]) for row in connection.execute(f"PRAGMA table_info({table})"))
            indexes = set()
            for index in connection.execute(f"PRAGMA index_list({table})"):
                if index[2] != 1 or index[4] != 0:
                    raise ValueError("database schema is unsupported")
                index_columns = tuple(row[2] for row in connection.execute(f"PRAGMA index_info({index[1]})"))
                indexes.add((index[3], index_columns))
            if columns != expected_columns or indexes != _SCHEMA_INDEXES[table]:
                raise ValueError("database schema is unsupported")
            foreign_keys = {
                (row[2], row[3], row[4], row[5], row[6], row[7])
                for row in connection.execute(f"PRAGMA foreign_key_list({table})")
            }
            expected_foreign_keys = (
                {("research_sessions", "session_id", "session_id", "NO ACTION", "CASCADE", "NONE")}
                if table in _CASCADE_SESSION_FOREIGN_KEY_TABLES
                else ({("research_acquisition_attempts", "attempt_id", "attempt_id", "NO ACTION", "CASCADE", "NONE")} if table == "research_acquisition_outcomes" else set())
            )
            if foreign_keys != expected_foreign_keys:
                raise ValueError("database schema is unsupported")
        check = [row[0] for row in connection.execute("PRAGMA quick_check")]
        if check != ["ok"]:
            raise ValueError("database quick_check failed")

    def _session(self, connection: sqlite3.Connection, session_id: str) -> ResearchSession:
        row = connection.execute("SELECT * FROM research_sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise ValueError("session does not exist")
        queries = tuple(QuerySpec(item[0]) for item in connection.execute("SELECT query_text FROM research_queries WHERE session_id = ? ORDER BY ordinal", (session_id,)))
        languages = tuple(item[0] for item in connection.execute("SELECT language FROM research_languages WHERE session_id = ? ORDER BY ordinal", (session_id,)))
        return ResearchSession(row["session_id"], row["topic"], queries, languages, FreshnessProfile(row["freshness_profile"]), row["discovery_fingerprint"], ResearchState(row["state"]), None if row["required_user_action"] is None else RequiredUserAction(row["required_user_action"]), row["revision"], None if row["retry_target"] is None else ResearchState(row["retry_target"]), _datetime(row["created_at"]), _datetime(row["updated_at"]))

    def _expected(self, connection: sqlite3.Connection, session_id: str, expected_revision: int, states: set[ResearchState]) -> ResearchSession:
        session = self._session(connection, session_id)
        if session.revision != expected_revision:
            raise ValueError("session revision conflict")
        if session.state not in states:
            raise ValueError("invalid session transition")
        return session

    def _transition(self, connection: sqlite3.Connection, session: ResearchSession, target: ResearchState, event_code: str, payload: object, *, required_action: RequiredUserAction | None = None, retry_target: ResearchState | None = None) -> ResearchSession:
        now = self._timestamp()
        completed_at = now if target in {ResearchState.COMPLETED, ResearchState.CANCELLED} else None
        connection.execute(
            """UPDATE research_sessions SET state = ?, required_user_action = ?, revision = ?, retry_target = ?, updated_at = ?, completed_at = ? WHERE session_id = ?""",
            (target.value, None if required_action is None else required_action.value, session.revision + 1, None if retry_target is None else retry_target.value, now, completed_at, session.session_id),
        )
        self._event(connection, session.session_id, session.state, target, event_code, payload)
        return self._session(connection, session.session_id)

    def _event(self, connection: sqlite3.Connection, session_id: str, from_state: ResearchState | None, to_state: ResearchState, code: str, payload: object) -> None:
        connection.execute(
            "INSERT INTO research_events(session_id, from_state, to_state, event_code, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, None if from_state is None else from_state.value, to_state.value, code, _canonical_json(payload), self._timestamp()),
        )

    def _decision(self, connection: sqlite3.Connection, key: str, session_id: str, expected_revision: int, action: str, payload: object, result: ResearchSession) -> None:
        stored = {"request": payload, "result": _session_payload(result)}
        connection.execute("INSERT INTO research_decisions VALUES (?, ?, ?, ?, ?, ?)", (key, session_id, expected_revision, action, _canonical_json(stored), self._timestamp()))

    def _idempotent_decision(self, connection: sqlite3.Connection, key: str, session_id: str, expected_revision: int, action: str, payload: object) -> ResearchSession | None:
        row = connection.execute("SELECT * FROM research_decisions WHERE idempotency_key = ?", (key,)).fetchone()
        if row is None:
            return None
        stored = json.loads(row["payload_json"])
        if row["session_id"] != session_id or row["expected_revision"] != expected_revision or row["action"] != action or stored.get("request") != payload:
            raise ValueError("idempotency key payload differs")
        result = stored.get("result")
        if not isinstance(result, dict):
            raise ValueError("stored decision result is invalid")
        return _session_from_payload(result)

    def _candidate_revision(self, connection: sqlite3.Connection, session_id: str) -> int:
        row = connection.execute("SELECT MAX(snapshot_revision) FROM research_candidates WHERE session_id = ?", (session_id,)).fetchone()
        if row is None or row[0] is None:
            raise ValueError("session has no candidates")
        return row[0]

    def _attempt_from_row(self, row: sqlite3.Row) -> AcquisitionAttempt:
        payload = json.loads(row["payload_json"])
        return AcquisitionAttempt(row["attempt_id"], row["idempotency_key"], row["session_id"], row["expected_revision"], row["status"], tuple(payload["video_ids"]), _datetime(row["created_at"]), _datetime(row["updated_at"]))

    def _timestamp(self) -> str:
        return _iso(self._now())


def _canonical_json(value: object) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _is_error_code(value: object) -> bool:
    return isinstance(value, str) and _ERROR_CODE.fullmatch(value) is not None


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must be timezone-aware")
    return parsed


def _session_payload(session: ResearchSession) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "topic": session.topic,
        "queries": [query.text for query in session.queries],
        "languages": list(session.languages),
        "freshness_profile": session.freshness_profile.value,
        "discovery_fingerprint": session.discovery_fingerprint,
        "state": session.state.value,
        "required_user_action": None if session.required_user_action is None else session.required_user_action.value,
        "revision": session.revision,
        "retry_target": None if session.retry_target is None else session.retry_target.value,
        "created_at": _iso(session.created_at),
        "updated_at": _iso(session.updated_at),
    }


def _session_from_payload(payload: dict[str, object]) -> ResearchSession:
    return ResearchSession(
        session_id=str(payload["session_id"]),
        topic=str(payload["topic"]),
        queries=tuple(QuerySpec(query) for query in payload["queries"]),  # type: ignore[arg-type]
        languages=tuple(payload["languages"]),  # type: ignore[arg-type]
        freshness_profile=FreshnessProfile(str(payload["freshness_profile"])),
        discovery_fingerprint=str(payload["discovery_fingerprint"]),
        state=ResearchState(str(payload["state"])),
        required_user_action=None if payload["required_user_action"] is None else RequiredUserAction(str(payload["required_user_action"])),
        revision=int(payload["revision"]),
        retry_target=None if payload["retry_target"] is None else ResearchState(str(payload["retry_target"])),
        created_at=_datetime(str(payload["created_at"])),
        updated_at=_datetime(str(payload["updated_at"])),
    )


def _assessment_from_json(payload_json: str) -> ResearchAssessment:
    payload = json.loads(payload_json)
    snapshot = DatabaseSnapshot(**payload["snapshot"])
    coverage_payload = payload["coverage"]
    coverage = CoverageMetrics(coverage_payload["matched_passages"], coverage_payload["matched_videos"], coverage_payload["distinct_channels"], tuple(coverage_payload["queries_with_zero_hits"]), coverage_payload["newest_source_published_at"], coverage_payload["unknown_publication_date_count"])
    freshness_payload = payload["freshness"]
    freshness = FreshnessAssessment(FreshnessProfile(freshness_payload["profile"]), freshness_payload["maximum_age_days"], None if freshness_payload["last_successful_discovery_at"] is None else _datetime(freshness_payload["last_successful_discovery_at"]), freshness_payload["stale"], freshness_payload["reason"])
    passages = tuple(PassageEvidence(**item) for item in payload["passages"])
    videos = tuple(
        VideoEvidence(**(item | {"source_keys": tuple(item["source_keys"])}))
        for item in payload["videos"]
    )
    return ResearchAssessment(_datetime(payload["created_at"]), snapshot, coverage, freshness, passages, videos)


def _candidate_from_json(payload_json: str) -> ResearchCandidate:
    payload: dict[str, Any] = json.loads(payload_json)
    payload["matched_queries"] = tuple(payload["matched_queries"])
    payload["status"] = CandidateStatus(payload["status"])
    return ResearchCandidate(**payload)
