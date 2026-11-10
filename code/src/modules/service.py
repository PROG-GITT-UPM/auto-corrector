"""What runs in the background while the API waits for requests.

* **Workers** take queued jobs from the database and grade them, each in its own container.
* **Score saver**: every `save_every` seconds writes the pending scores to Classroom 50.
* **Watcher**: every `watch_tick` seconds checks which assignments with watching enabled are due
  (their own interval has passed) and queues the students who have pushed since the last round.

The queue lives in PostgreSQL, so jobs survive a restart: jobs left half-way are queued again on
start-up, and pending scores are written on the next round.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from ..providers.database import Assignment
from ..providers.sandbox import remove_leftovers
from .grading import Context, grade_submission

log = logging.getLogger(__name__)


class GradingService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.db = ctx.db
        self.stop_event = threading.Event()
        self.wake = threading.Event()   # set when a job is queued, so workers start at once
        self.threads: list = []

    # ─── Queue ───────────────────────────────────────────────────────────────

    def enqueue(self, assignment: Assignment, user: str, force: bool = False,
                until: Optional[str] = None, source: str = "api") -> Optional[str]:
        ident = self.db.enqueue(assignment.classroom, assignment.slug, user, force, until, source)
        if ident:
            self.wake.set()
        return ident

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                job = self.db.claim_job()
            except Exception:
                log.exception("could not read the queue")
                self.stop_event.wait(5)
                continue
            if job is None:
                self.wake.wait(2)
                self.wake.clear()
                continue
            try:
                assignment = self.db.get_assignment(job["classroom"], job["slug"])
                if assignment is None:
                    raise RuntimeError(f"assignment {job['classroom']}/{job['slug']} no longer "
                                       "exists")
                summary = grade_submission(self.ctx, assignment, job["username"],
                                           until=job["until"], force=job["force"])
                self.db.finish_job(job["id"], summary)
                log.info("%s/%s %s: %s %s", job["classroom"], job["slug"], job["username"],
                         summary.get("status"),
                         f"{summary.get('points')}/{summary.get('max_points')}"
                         if "points" in summary else "")
            except Exception as exc:  # a failing submission never stops the service
                log.exception("failure grading %s", job["username"])
                self.db.fail_job(job["id"], str(exc) or type(exc).__name__)

    # ─── Scores ──────────────────────────────────────────────────────────────

    def save_scores(self) -> dict:
        statuses = self.ctx.scores.save()
        if statuses:
            log.info("scores saved to Classroom 50: %d", len(statuses))
        for (classroom, slug, user), status in statuses.items():
            if status == "kept":
                log.warning("%s/%s %s: has a manual score from a teacher; not changed",
                            classroom, slug, user)
        return statuses

    def _saver(self) -> None:
        while not self.stop_event.wait(self.ctx.settings.save_every):
            try:
                if self.db.pending_count():
                    self.save_scores()
            except Exception:
                log.exception("could not save the scores; will retry")

    # ─── Watching ────────────────────────────────────────────────────────────

    def watch_assignment(self, assignment: Assignment) -> int:
        """Queues the students who pushed since the last round. Returns how many were queued."""
        seen = self.db.watch_state(assignment.classroom, assignment.slug)
        queued = 0
        for user, repo, pushed_at in self.ctx.repos.list_repos(assignment):
            if pushed_at and seen.get(repo) == pushed_at:
                continue
            # A submission already graded with the same tests ends quickly as "already graded".
            if self.enqueue(assignment, user, source="watch"):
                queued += 1
            self.db.set_pushed(assignment.classroom, assignment.slug, repo, pushed_at)
        self.db.mark_watched(assignment.classroom, assignment.slug)
        return queued

    def _watcher(self) -> None:
        while not self.stop_event.is_set():
            try:
                for assignment in self.db.due_for_watch():
                    try:
                        n = self.watch_assignment(assignment)
                        if n:
                            log.info("watch %s: %d new submission(s) queued", assignment.key, n)
                    except Exception:
                        log.exception("watch failure on %s; trying again next round",
                                      assignment.key)
                        self.db.mark_watched(assignment.classroom, assignment.slug)
            except Exception:
                log.exception("could not read the watched assignments")
            self.stop_event.wait(self.ctx.settings.watch_tick)

    # ─── Life cycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self.ctx.settings.sandbox.get("type") == "docker":
            removed = remove_leftovers()
            if removed:
                log.info("removed %d sandbox container(s) left by the last stop", removed)
        requeued = self.db.requeue_interrupted()
        if requeued:
            log.info("%d job(s) interrupted by the last stop queued again", requeued)
        for i in range(self.ctx.settings.workers):
            self.threads.append(threading.Thread(target=self._worker, name=f"worker-{i}",
                                                 daemon=True))
        self.threads.append(threading.Thread(target=self._saver, name="scores", daemon=True))
        self.threads.append(threading.Thread(target=self._watcher, name="watch", daemon=True))
        for t in self.threads:
            t.start()

    def stop(self, timeout: float = 30) -> None:
        """Stops the background threads and writes the pending scores."""
        self.stop_event.set()
        self.wake.set()
        for t in self.threads:
            t.join(timeout)
        try:
            if self.db.pending_count():
                self.save_scores()
        except Exception:
            log.exception("could not save the pending scores on shutdown; they stay pending")
