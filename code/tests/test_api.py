"""The API end to end: FastAPI app, background workers, PostgreSQL and the fake GitHub."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from main import create_app

from conftest import API_TOKEN, ORG

AUTH = {"Authorization": f"Bearer {API_TOKEN}"}


@pytest.fixture
def client(settings, fake_github):
    app = create_app()
    app.state.settings = settings
    with TestClient(app) as c:
        yield c


def wait_for(client, condition, seconds=30):
    limit = time.time() + seconds
    while time.time() < limit:
        if condition():
            return True
        time.sleep(0.2)
    return False


def add_lab_2(client):
    r = client.put("/assignments/grupo-test/lab-2", headers=AUTH,
                   json={"template": f"{ORG}/prog-lab-2"})
    assert r.status_code == 200, r.text
    return r.json()


def test_token_is_required(client):
    assert client.get("/health").status_code == 200
    assert client.get("/assignments").status_code == 401
    assert client.get("/assignments", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/docs").status_code == 200


def test_add_assignment_checks_classroom_50(client):
    body = add_lab_2(client)
    assert body["warnings"] == [] and body["watch"]["enabled"] is False
    r = client.put("/assignments/grupo-test/lab-2-b", headers=AUTH,
                   json={"template": f"{ORG}/prog-lab-2"})
    assert "autograder" in " ".join(r.json()["warnings"])     # it still uses Actions
    r = client.put("/assignments/grupo-test/missing", headers=AUTH,
                   json={"template": f"{ORG}/prog-lab-2"})
    assert r.status_code == 422
    r = client.put("/assignments/grupo-test/lab-2", headers=AUTH,
                   json={"template": f"{ORG}/does-not-exist"})
    assert r.status_code == 422


def test_grade_on_request_and_save_scores(client, fake_github):
    add_lab_2(client)
    students = client.get("/assignments/grupo-test/lab-2/students", headers=AUTH).json()
    assert sorted(s["user"] for s in students) == ["ana", "luis"]
    r = client.post("/grade", headers=AUTH, json={"classroom": "grupo-test",
                                                   "assignment": "lab-2"})
    assert r.status_code == 202 and len(r.json()["jobs"]) == 2

    def done():
        jobs = client.get("/jobs", headers=AUTH).json()
        return jobs and all(j["status"] in ("done", "error") for j in jobs)
    assert wait_for(client, done)
    jobs = {j["user"]: j for j in client.get("/jobs", headers=AUTH).json()}
    assert jobs["ana"]["points"] == 9 and jobs["luis"]["points"] == 0
    assert jobs["ana"]["release_url"] and len(jobs["ana"]["tests"]) == 9
    one = client.get(f"/jobs/{jobs['ana']['id']}", headers=AUTH).json()
    assert one["outcome"] == "graded"

    assert len(client.get("/scores/pending", headers=AUTH).json()) == 2
    saved = client.post("/scores/save", headers=AUTH).json()
    assert {s["user"]: s["result"] for s in saved} == {"ana": "new", "luis": "new"}
    assert client.get("/scores/pending", headers=AUTH).json() == []
    assert len(fake_github.content_commits) == 1 and "[skip ci]" in fake_github.content_commits[0]


def test_watch_can_be_turned_on_and_off(client, fake_github):
    add_lab_2(client)
    r = client.put("/assignments/grupo-test/lab-2/watch", headers=AUTH,
                   json={"enabled": True, "interval_seconds": 30})
    assert r.json()["watch"]["enabled"] is True

    def watched():
        jobs = client.get("/jobs?source=watch", headers=AUTH).json()
        return len([j for j in jobs if j["source"] == "watch" and j["status"] == "done"]) == 2
    assert wait_for(client, watched)

    client.put("/assignments/grupo-test/lab-2/watch", headers=AUTH, json={"enabled": False})
    fake_github.commit("grupo-test-lab-2-ana", {}, "2026-11-09T09:00:00Z")
    time.sleep(2)                                    # several watch ticks
    assert len(client.get("/jobs", headers=AUTH).json()) == 2   # nothing new while it is off
    # A manual round still works with watching off, and only takes the new push.
    assert client.post("/assignments/grupo-test/lab-2/watch/run",
                       headers=AUTH).json() == {"queued": 1}


def test_hidden_tests_api(client):
    add_lab_2(client)
    hidden = [{"name": "caso oculto", "type": "io", "run": "python exercise_02_dni.py",
               "input": "00000000\n", "expected": "T", "points": 1}]
    r = client.put("/assignments/grupo-test/lab-2/hidden-tests", headers=AUTH,
                   json={"tests": hidden})
    assert r.status_code == 200 and r.json()["points"] == 1
    assert client.get("/assignments/grupo-test/lab-2", headers=AUTH).json()["hidden_tests"] == 1
    clash = [{**hidden[0], "name": "Ejercicio 2 — Letra del DNI · ejemplo 1"}]
    r = client.put("/assignments/grupo-test/lab-2/hidden-tests", headers=AUTH,
                   json={"tests": clash})
    assert r.status_code == 422                      # name already used by a visible test
    r = client.put("/assignments/grupo-test/lab-2/hidden-tests", headers=AUTH,
                   json={"tests": [{"name": "x", "type": "io"}]})
    assert r.status_code == 422                      # invalid test
    # Updating the assignment keeps its hidden tests.
    add_lab_2(client)
    assert client.get("/assignments/grupo-test/lab-2", headers=AUTH).json()["hidden_tests"] == 1
    assert client.delete("/assignments/grupo-test/lab-2/hidden-tests",
                         headers=AUTH).status_code == 204


def test_health(client):
    body = client.get("/health").json()
    assert body["database"] is True and body["status"] == "ok"
