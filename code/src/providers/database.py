"""PostgreSQL: the whole state of the service.

Everything the service needs to resume where it stopped lives here, so copying the database volume
to another machine moves the service with it:

* `assignments`: what is graded, its hidden tests and whether its pushes are watched;
* `watch_state`: the last push seen on each student repository;
* `jobs`: the queue and the grading history (a queued job is a row with status `queued`);
* `pending_scores`: scores graded but not yet written to Classroom 50.

The schema is created and upgraded on start-up (`migrate`), so a new or older database works.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

# Each entry upgrades the schema by one version. Never edit an entry that has been released:
# add a new one.
MIGRATIONS = [
    """
    CREATE TABLE assignments (
        classroom        text NOT NULL,
        slug             text NOT NULL,
        template         text NOT NULL,
        ref              text NOT NULL DEFAULT 'main',
        grader_dir       text NOT NULL DEFAULT '.grader',
        publish_release  boolean NOT NULL DEFAULT true,
        save_score       boolean NOT NULL DEFAULT true,
        hidden_tests     jsonb,
        watch_enabled    boolean NOT NULL DEFAULT false,
        watch_interval   integer NOT NULL DEFAULT 120,
        last_watch_at    timestamptz,
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (classroom, slug)
    );
    CREATE TABLE watch_state (
        classroom  text NOT NULL,
        slug       text NOT NULL,
        repo       text NOT NULL,
        pushed_at  text NOT NULL,
        PRIMARY KEY (classroom, slug, repo),
        FOREIGN KEY (classroom, slug) REFERENCES assignments ON DELETE CASCADE
    );
    CREATE TABLE jobs (
        id           text PRIMARY KEY,
        classroom    text NOT NULL,
        slug         text NOT NULL,
        username     text NOT NULL,
        force        boolean NOT NULL DEFAULT false,
        until        text,
        source       text NOT NULL,
        status       text NOT NULL CHECK (status IN ('queued', 'grading', 'done', 'error')),
        created_at   timestamptz NOT NULL DEFAULT now(),
        started_at   timestamptz,
        finished_at  timestamptz,
        commit       text,
        points       integer,
        max_points   integer,
        late         boolean,
        release_url  text,
        outcome      text,
        summary      jsonb,
        error        text
    );
    -- At most one queued job per student and assignment: asking twice does not grade twice.
    CREATE UNIQUE INDEX jobs_one_queued ON jobs (classroom, slug, lower(username))
        WHERE status = 'queued';
    CREATE INDEX jobs_status_created ON jobs (status, created_at);
    CREATE INDEX jobs_assignment ON jobs (classroom, slug, created_at DESC);
    CREATE TABLE pending_scores (
        id               bigserial PRIMARY KEY,
        classroom        text NOT NULL,
        slug             text NOT NULL,
        owner            text NOT NULL,
        assignment_type  text NOT NULL,
        record           jsonb NOT NULL,
        force            boolean NOT NULL DEFAULT false,
        created_at       timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX pending_scores_owner ON pending_scores (classroom, slug, lower(owner));
    """,
]


@dataclass
class Assignment:
    """A Classroom 50 assignment graded by this service."""
    classroom: str
    slug: str
    template: str                        # "org/repo" of the assignment: the tests come from here
    ref: str = "main"
    grader_dir: str = ".grader"
    publish_release: bool = True         # Release with the result in the student's repository
    save_score: bool = True              # score in the Classroom 50 scores.json
    hidden_tests: Optional[list] = None  # tests students do not see (Classroom 50 format)
    watch_enabled: bool = False
    watch_interval: int = 120
    last_watch_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def key(self) -> str:
        return f"{self.classroom}/{self.slug}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, url: str, max_size: int = 10) -> None:
        self.pool = ConnectionPool(url, min_size=1, max_size=max_size, open=True,
                                   kwargs={"row_factory": dict_row, "autocommit": True})

    def close(self) -> None:
        self.pool.close()

    def ping(self) -> bool:
        try:
            with self.pool.connection(timeout=5) as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    # ─── Schema ──────────────────────────────────────────────────────────────

    def migrate(self) -> int:
        """Brings the schema up to date. Returns the schema version."""
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(4242)")  # one migrator at a time
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version integer NOT NULL)")
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            version = row["version"] if row else 0
            if not row:
                conn.execute("INSERT INTO schema_version VALUES (0)")
            for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
                conn.execute(sql)
                conn.execute("UPDATE schema_version SET version = %s", (i,))
            return len(MIGRATIONS)

    # ─── Assignments ─────────────────────────────────────────────────────────

    @staticmethod
    def _assignment(row: dict) -> Assignment:
        return Assignment(**{k: row[k] for k in Assignment.__dataclass_fields__})

    def list_assignments(self) -> list:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT * FROM assignments ORDER BY classroom, slug").fetchall()
        return [self._assignment(r) for r in rows]

    def get_assignment(self, classroom: str, slug: str) -> Optional[Assignment]:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT * FROM assignments WHERE classroom = %s AND slug = %s",
                               (classroom, slug)).fetchone()
        return self._assignment(row) if row else None

    def upsert_assignment(self, classroom: str, slug: str, template: str, ref: str,
                          publish_release: bool, save_score: bool) -> Assignment:
        with self.pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO assignments (classroom, slug, template, ref, publish_release,
                                            save_score)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (classroom, slug) DO UPDATE SET
                       template = EXCLUDED.template, ref = EXCLUDED.ref,
                       publish_release = EXCLUDED.publish_release,
                       save_score = EXCLUDED.save_score, updated_at = now()
                   RETURNING *""",
                (classroom, slug, template, ref, publish_release, save_score)).fetchone()
        return self._assignment(row)

    def delete_assignment(self, classroom: str, slug: str) -> bool:
        with self.pool.connection() as conn:
            cur = conn.execute("DELETE FROM assignments WHERE classroom = %s AND slug = %s",
                               (classroom, slug))
            return cur.rowcount > 0

    def set_watch(self, classroom: str, slug: str, enabled: bool,
                  interval: int) -> Optional[Assignment]:
        with self.pool.connection() as conn:
            row = conn.execute(
                """UPDATE assignments SET watch_enabled = %s, watch_interval = %s,
                          last_watch_at = CASE WHEN %s THEN last_watch_at ELSE NULL END,
                          updated_at = now()
                   WHERE classroom = %s AND slug = %s RETURNING *""",
                (enabled, interval, enabled, classroom, slug)).fetchone()
        return self._assignment(row) if row else None

    def set_hidden_tests(self, classroom: str, slug: str,
                         tests: Optional[list]) -> Optional[Assignment]:
        with self.pool.connection() as conn:
            row = conn.execute(
                """UPDATE assignments SET hidden_tests = %s, updated_at = now()
                   WHERE classroom = %s AND slug = %s RETURNING *""",
                (Jsonb(tests) if tests is not None else None, classroom, slug)).fetchone()
        return self._assignment(row) if row else None

    def due_for_watch(self) -> list:
        """Watched assignments whose interval has passed since their last round."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM assignments WHERE watch_enabled AND (last_watch_at IS NULL
                   OR last_watch_at + make_interval(secs => watch_interval) <= now())
                   ORDER BY classroom, slug""").fetchall()
        return [self._assignment(r) for r in rows]

    def mark_watched(self, classroom: str, slug: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE assignments SET last_watch_at = now() "
                         "WHERE classroom = %s AND slug = %s", (classroom, slug))

    # ─── Watch state ─────────────────────────────────────────────────────────

    def watch_state(self, classroom: str, slug: str) -> dict:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT repo, pushed_at FROM watch_state "
                                "WHERE classroom = %s AND slug = %s", (classroom, slug)).fetchall()
        return {r["repo"]: r["pushed_at"] for r in rows}

    def set_pushed(self, classroom: str, slug: str, repo: str, pushed_at: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO watch_state VALUES (%s, %s, %s, %s)
                   ON CONFLICT (classroom, slug, repo) DO UPDATE SET pushed_at = EXCLUDED.pushed_at""",
                (classroom, slug, repo, pushed_at))

    # ─── Jobs ────────────────────────────────────────────────────────────────

    def enqueue(self, classroom: str, slug: str, username: str, force: bool = False,
                until: Optional[str] = None, source: str = "api") -> Optional[str]:
        """Queues a grading job. Returns its id, or None if that student already has one queued."""
        ident = uuid.uuid4().hex[:12]
        with self.pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO jobs (id, classroom, slug, username, force, until, source, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued')
                   ON CONFLICT (classroom, slug, lower(username)) WHERE status = 'queued'
                   DO NOTHING RETURNING id""",
                (ident, classroom, slug, username, force, until, source)).fetchone()
        return row["id"] if row else None

    def claim_job(self) -> Optional[dict]:
        """Takes the oldest queued job and marks it as being graded (safe with many workers)."""
        with self.pool.connection() as conn:
            return conn.execute(
                """UPDATE jobs SET status = 'grading', started_at = now()
                   WHERE id = (SELECT id FROM jobs WHERE status = 'queued'
                               ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
                   RETURNING *""").fetchone()

    def finish_job(self, ident: str, summary: dict) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE jobs SET status = 'done', finished_at = now(), commit = %s,
                          points = %s, max_points = %s, late = %s, release_url = %s,
                          outcome = %s, summary = %s
                   WHERE id = %s""",
                (summary.get("commit"), summary.get("points"), summary.get("max_points"),
                 summary.get("late"), summary.get("release"), summary.get("status"),
                 Jsonb(summary), ident))

    def fail_job(self, ident: str, error: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE jobs SET status = 'error', finished_at = now(), error = %s "
                         "WHERE id = %s", (error[:4000], ident))

    def requeue_interrupted(self) -> int:
        """Jobs left half-way when the service stopped go back to the queue."""
        with self.pool.connection() as conn, conn.transaction():
            # If the student has queued again meanwhile, the interrupted job is redundant.
            conn.execute(
                """UPDATE jobs j SET status = 'error', finished_at = now(),
                          error = 'interrupted; superseded by a newer queued job'
                   WHERE status = 'grading' AND EXISTS (
                       SELECT 1 FROM jobs q WHERE q.status = 'queued'
                       AND q.classroom = j.classroom AND q.slug = j.slug
                       AND lower(q.username) = lower(j.username))""")
            cur = conn.execute("UPDATE jobs SET status = 'queued', started_at = NULL "
                               "WHERE status = 'grading'")
            return cur.rowcount

    def get_job(self, ident: str) -> Optional[dict]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM jobs WHERE id = %s", (ident,)).fetchone()

    def list_jobs(self, classroom: Optional[str] = None, slug: Optional[str] = None,
                  username: Optional[str] = None, status: Optional[str] = None,
                  limit: int = 100) -> list:
        conditions, params = [], []
        for column, value in (("classroom", classroom), ("slug", slug), ("status", status)):
            if value:
                conditions.append(f"{column} = %s")
                params.append(value)
        if username:
            conditions.append("lower(username) = lower(%s)")
            params.append(username)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.pool.connection() as conn:
            return conn.execute(f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT %s",
                                (*params, limit)).fetchall()

    def queue_counts(self) -> dict:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT status, count(*) AS n FROM jobs "
                                "WHERE status IN ('queued', 'grading') GROUP BY status").fetchall()
        counts = {"queued": 0, "grading": 0}
        counts.update({r["status"]: r["n"] for r in rows})
        return counts

    # ─── Pending scores ──────────────────────────────────────────────────────

    def add_pending_score(self, classroom: str, slug: str, assignment_type: str, record: dict,
                          force: bool = False) -> None:
        """A newer score for the same student replaces the pending one."""
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO pending_scores (classroom, slug, owner, assignment_type, record,
                                               force)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (classroom, slug, lower(owner)) DO UPDATE SET
                       id = nextval('pending_scores_id_seq'),
                       assignment_type = EXCLUDED.assignment_type, record = EXCLUDED.record,
                       force = EXCLUDED.force, created_at = now()""",
                (classroom, slug, record["owner"], assignment_type, Jsonb(record), force))

    def pending_scores(self) -> list:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM pending_scores ORDER BY classroom, id").fetchall()

    def delete_pending_scores(self, ids: list) -> None:
        """Deletes exactly these rows; a score replaced meanwhile has a new id and stays."""
        if not ids:
            return
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM pending_scores WHERE id = ANY(%s)", (list(ids),))

    def pending_count(self) -> int:
        with self.pool.connection() as conn:
            return conn.execute("SELECT count(*) AS n FROM pending_scores").fetchone()["n"]


def to_json(value: Any) -> Any:
    """JSON-friendly copy of a row (datetimes as ISO 8601 UTC)."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_json(v) for v in value]
    return value


def dumps(value: Any) -> str:
    return json.dumps(to_json(value), ensure_ascii=False)
