"""The service: push watching, queue, HTTP API with token and batched score saving."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from grader.service import Service, _handler

from test_cycle import env  # noqa: F401  (shared fixture)


def wait_for(condition, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


def test_watch_grades_and_saves(env):  # noqa: F811
    gh, ctx, assignment = env
    ctx.cfg.service = {"watch": [assignment.key], "watch_every": 0, "save_every": 3600}
    service = Service(ctx)
    service.start_threads()
    try:
        assert service.watch_once() == 2                    # ana and luis
        assert wait_for(lambda: all(j["status"] in ("done", "error")
                                    for j in service.jobs.values()))
        assert {j["status"] for j in service.jobs.values()} == {"done"}
        assert service.watch_once() == 0                    # no new pushes, nothing
        gh.commit("grupo-test-lab-02-prueba-ana", {}, "2026-11-09T09:00:00Z")
        assert service.watch_once() == 1                    # only the one who pushed
        assert wait_for(lambda: service.queue.unfinished_tasks == 0)
    finally:
        service.shutdown()                                  # saves pending scores
    scores = json.loads(gh.read("classroom50", "grupo-test/scores.json"))
    assert {e["owner"] for e in scores["assignments"]["lab-02-prueba"]["entries"]} == {"ana", "luis"}
    assert len(gh.content_commits) == 1                     # a single commit for everything


def test_api_with_token(env):  # noqa: F811
    gh, ctx, assignment = env
    service = Service(ctx)
    service.start_threads()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(service, "secret"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def request(path, data=None, token="secret"):
        req = urllib.request.Request(base + path, method="POST" if data is not None else "GET",
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        assert request("/health", token="")[0] == 200
        assert request("/jobs", token="other")[0] == 401
        assert request("/grade", {"classroom": "grupo-test"})[0] == 400
        code, resp = request("/grade", {"classroom": "grupo-test", "assignment": "lab-02-prueba",
                                        "students": ["ana"]})
        assert code == 202 and len(resp["jobs"]) == 1
        ident = resp["jobs"][0]
        assert wait_for(lambda: request(f"/jobs/{ident}")[1]["status"] == "done")
        assert request(f"/jobs/{ident}")[1]["summary"]["points"] == 9
    finally:
        server.shutdown()
        service.shutdown()
