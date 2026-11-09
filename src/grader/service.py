"""The grader as an always-on service.

* An **HTTP API** to request grading runs (`POST /grade`) and check how they are going.
* A **queue** with N workers: each submission is graded in its own container.
* **Watching** (optional): every `watch_every` seconds it checks which repositories of the
  watched assignments have received a push and puts them in the queue. One paginated query to
  the organization per round is enough, so it does not use up the API rate limit, and GitHub does
  not need to reach the server (no webhook, no open ports).
* Scores are saved to Classroom 50 in batches, every `save_every` seconds.

Standard library only. Meant to sit behind a proxy (nginx) or on the internal network.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import signal
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .grading import Context, grade_submission

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Service:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        conf = ctx.cfg.service
        self.workers = int(conf.get("workers", 2))
        self.watch = list(conf.get("watch", []))
        self.watch_every = int(conf.get("watch_every", 120))
        self.save_every = int(conf.get("save_every", 30))
        self.queue: queue.Queue = queue.Queue()
        self.jobs: OrderedDict = OrderedDict()
        self.queued: set = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.watch_state = ctx.cfg.work_dir / "watch_state.json"

    # ─── Jobs ────────────────────────────────────────────────────────────────

    def enqueue(self, classroom: str, slug: str, user: str, force: bool = False,
                until: Optional[str] = None, source: str = "api") -> Optional[str]:
        """Puts a submission in the queue. If it was already waiting, it is not duplicated."""
        self.ctx.cfg.assignment(classroom, slug)  # error if the assignment is not configured
        key = (classroom, slug, user.lower())
        with self.lock:
            if key in self.queued:
                return None
            ident = uuid.uuid4().hex[:12]
            self.jobs[ident] = {"id": ident, "classroom": classroom, "assignment": slug,
                                "user": user, "force": force, "until": until, "source": source,
                                "status": "queued", "created": _now()}
            while len(self.jobs) > 2000:
                self.jobs.popitem(last=False)
            self.queued.add(key)
        self.queue.put(ident)
        return ident

    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                ident = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            with self.lock:
                job = self.jobs.get(ident)
                if job is None:          # dropped for being old while it was waiting
                    self.queue.task_done()
                    continue
                self.queued.discard((job["classroom"], job["assignment"], job["user"].lower()))
                job["status"] = "grading"
                job["started"] = _now()
            try:
                assignment = self.ctx.cfg.assignment(job["classroom"], job["assignment"])
                summary = grade_submission(self.ctx, assignment, job["user"],
                                           until=job.get("until"), force=job.get("force", False))
                with self.lock:
                    job.update(status="done", summary=summary, finished=_now())
                log.info("%s/%s %s: %s %s", job["classroom"], job["assignment"], job["user"],
                         summary.get("status"),
                         f"{summary.get('points')}/{summary.get('max_points')}"
                         if "points" in summary else "")
            except Exception as exc:  # a failure in one submission does not bring the service down
                log.exception("failure grading %s", job["user"])
                with self.lock:
                    job.update(status="error", error=str(exc), finished=_now())
            finally:
                self.queue.task_done()

    # ─── Scores ──────────────────────────────────────────────────────────────

    def _save_scores(self) -> None:
        try:
            if self.ctx.scores.pending():
                statuses = self.ctx.scores.save()
                log.info("scores saved to Classroom 50: %d", len(statuses))
                for (classroom, slug, user), status in statuses.items():
                    if status == "kept":
                        log.warning("%s/%s %s: has a manual score from the teacher; not changed",
                                    classroom, slug, user)
        except Exception:
            log.exception("could not save the scores; will retry")

    def _saver(self) -> None:
        while not self.stop.wait(self.save_every):
            self._save_scores()

    # ─── Watching ────────────────────────────────────────────────────────────

    def _read_state(self) -> dict:
        try:
            return json.loads(self.watch_state.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def watch_once(self) -> int:
        """Enqueues the repositories with new pushes since the last round."""
        state = self._read_state()
        new = 0
        for key in self.watch:
            assignment = self.ctx.cfg.assignment(*key.split("/", 1))
            for user, repo, pushed_at in self.ctx.repos.list_repos(assignment):
                if pushed_at and state.get(repo) == pushed_at:
                    continue
                # If the submission is already graded, the job finishes right away
                # ("already graded").
                if self.enqueue(assignment.classroom, assignment.slug, user, source="watch"):
                    new += 1
                state[repo] = pushed_at
        self.watch_state.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.watch_state.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=1), encoding="utf-8")
        temporary.replace(self.watch_state)
        return new

    def _watcher(self) -> None:
        while not self.stop.is_set():
            try:
                n = self.watch_once()
                if n:
                    log.info("watch: %d new submission(s) queued", n)
            except Exception:
                log.exception("watch failure; trying again next round")
            self.stop.wait(self.watch_every)

    # ─── Startup ─────────────────────────────────────────────────────────────

    def start_threads(self) -> None:
        for i in range(self.workers):
            threading.Thread(target=self._worker, name=f"worker-{i}", daemon=True).start()
        threading.Thread(target=self._saver, name="scores", daemon=True).start()
        if self.watch and self.watch_every > 0:
            threading.Thread(target=self._watcher, name="watch", daemon=True).start()

    def shutdown(self) -> None:
        self.stop.set()
        self._save_scores()


def _handler(service: Service, api_token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "grader"

        def log_message(self, format: str, *args: object) -> None:
            log.debug("%s %s", self.address_string(), format % args)

        def _respond(self, code: int, data: object) -> None:
            body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not api_token:
                return True
            received = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if hmac.compare_digest(received, api_token):
                return True
            self._respond(401, {"error": "missing or invalid token (Authorization: Bearer …)"})
            return False

        def do_GET(self) -> None:
            if self.path == "/health":
                return self._respond(200, {"ok": True, "queue": service.queue.qsize(),
                                           "pending_scores": service.ctx.scores.pending()})
            if not self._authorized():
                return
            if self.path.startswith("/jobs/"):
                ident = self.path.rsplit("/", 1)[1]
                with service.lock:
                    job = service.jobs.get(ident)
                return self._respond(200, job) if job else self._respond(404, {"error": "not found"})
            if self.path.split("?")[0] == "/jobs":
                with service.lock:
                    latest = list(service.jobs.values())[-100:]
                return self._respond(200, list(reversed(latest)))
            self._respond(404, {"error": "unknown path"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if self.path not in ("/grade", "/watch"):
                return self._respond(404, {"error": "unknown path"})
            if self.path == "/watch":
                return self._respond(200, {"queued": service.watch_once()})
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length) or b"{}")
                classroom, slug = data["classroom"], data["assignment"]
                assignment = service.ctx.cfg.assignment(classroom, slug)
                students = data.get("students") or [u for u, _, _ in
                                                    service.ctx.repos.list_repos(assignment)]
                ids = [i for u in students if (i := service.enqueue(
                    classroom, slug, u, force=bool(data.get("force")), until=data.get("until")))]
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                return self._respond(400, {"error": f"invalid request: {exc}"})
            self._respond(202, {"jobs": ids, "students": len(students)})

    return Handler


def serve(ctx: Context, host: Optional[str] = None, port: Optional[int] = None) -> None:
    conf = ctx.cfg.service
    host = host or conf.get("host", "127.0.0.1")
    port = int(port or conf.get("port", 8080))
    api_token = os.environ.get(conf.get("api_token_env", "GRADER_API_TOKEN"), "")
    if not api_token and host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("without an API token the service can only listen on 127.0.0.1")

    service = Service(ctx)
    service.start_threads()
    server = ThreadingHTTPServer((host, port), _handler(service, api_token))

    def _shut_down(*_: object) -> None:
        log.info("shutting down: saving pending scores")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shut_down)
    signal.signal(signal.SIGINT, _shut_down)
    log.info("grader listening on http://%s:%d (watching: %s)", host, port,
             ", ".join(service.watch) or "nothing")
    try:
        server.serve_forever()
    finally:
        service.shutdown()
        server.server_close()
