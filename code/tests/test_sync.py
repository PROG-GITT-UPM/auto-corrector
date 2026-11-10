"""Syncing the service's assignments with the ones declared in Classroom 50."""
import json

import pytest
from fastapi.testclient import TestClient

from main import create_app
from src.modules.assignments import sync

from conftest import API_TOKEN, ORG

AUTH = {"Authorization": f"Bearer {API_TOKEN}"}


@pytest.fixture
def classrooms(fake_github):
    """Adds a second classroom and, in grupo-test, a group assignment and one using Actions."""
    data = json.loads(fake_github.read("classroom50", "grupo-test/assignments.json"))
    template = {"owner": ORG, "repo": "prog-lab-2", "branch": "main"}
    data["assignments"] += [
        {"slug": "group-lab", "name": "Group", "mode": "group", "template": template,
         "no_autograder": True},
        {"slug": "uses-actions", "name": "Actions", "mode": "individual", "template": template},
    ]
    fake_github.put_file("classroom50", "grupo-test/assignments.json", json.dumps(data))
    fake_github.put_file("classroom50", "grupo-b/assignments.json", json.dumps({
        "schema": "classroom50/assignments/v1",
        "assignments": [{"slug": "lab-2", "name": "Lab 2", "mode": "individual",
                         "template": template, "no_autograder": True}]}))
    fake_github.put_file("classroom50", ".github/workflows/publish-pages.yaml", "on: push")
    return fake_github


def by_key(items):
    return {(i.classroom, i.assignment): i for i in items}


def test_sync_adds_skips_and_reports(env, classrooms):
    gh, ctx, _ = env
    ctx.db.delete_assignment("grupo-test", "lab-2")
    ctx.db.upsert_assignment("grupo-test", "old-lab", f"{ORG}/prog-lab-2", "main", True, True)

    preview = by_key(sync(ctx, dry_run=True))
    assert preview[("grupo-test", "lab-2")].action == "added"
    assert ctx.db.get_assignment("grupo-test", "lab-2") is None          # dry run: no changes

    items = by_key(sync(ctx))
    assert items[("grupo-test", "lab-2")].action == "added"
    assert items[("grupo-b", "lab-2")].action == "added"
    assert items[("grupo-test", "lab-2-b")].reason == "it has no template"
    assert "group" in items[("grupo-test", "group-lab")].reason
    assert "autograder" in items[("grupo-test", "uses-actions")].reason
    assert items[("grupo-test", "old-lab")].action == "missing"
    added = ctx.db.get_assignment("grupo-test", "lab-2")
    assert added.template == f"{ORG}/prog-lab-2" and added.watch_enabled is False
    assert ctx.db.get_assignment("grupo-test", "old-lab") is not None    # kept by default

    again = by_key(sync(ctx, remove_missing=True))
    assert again[("grupo-test", "lab-2")].action == "unchanged"
    assert again[("grupo-test", "old-lab")].action == "removed"
    assert ctx.db.get_assignment("grupo-test", "old-lab") is None


def test_sync_updates_template_and_keeps_settings(env, classrooms):
    gh, ctx, _ = env
    ctx.db.set_hidden_tests("grupo-test", "lab-2", [
        {"name": "oculta", "type": "run", "run": "true", "points": 1}])
    ctx.db.set_watch("grupo-test", "lab-2", True, 60)
    gh.commit("prog-lab-2-v2", gh.repos["prog-lab-2"]["commits"][0]["files"],
              "2026-11-02T10:00:00Z")
    data = json.loads(gh.read("classroom50", "grupo-test/assignments.json"))
    data["assignments"][0]["template"]["repo"] = "prog-lab-2-v2"
    gh.put_file("classroom50", "grupo-test/assignments.json", json.dumps(data))

    items = by_key(sync(ctx, classroom="grupo-test"))
    assert items[("grupo-test", "lab-2")].action == "updated"
    assert ("grupo-b", "lab-2") not in items                             # only that classroom
    a = ctx.db.get_assignment("grupo-test", "lab-2")
    assert a.template == f"{ORG}/prog-lab-2-v2"
    assert a.hidden_tests and a.watch_enabled and a.watch_interval == 60


def test_sync_endpoint(settings, classrooms):
    app = create_app()
    app.state.settings = settings
    with TestClient(app) as client:
        r = client.post("/assignments/sync", headers=AUTH, json={"dry_run": True})
        assert r.status_code == 200
        assert {(i["classroom"], i["assignment"]): i["action"] for i in r.json()}[
            ("grupo-test", "lab-2")] == "added"
        assert client.get("/assignments", headers=AUTH).json() == []
        r = client.post("/assignments/sync", headers=AUTH, json={})
        assert len(client.get("/assignments", headers=AUTH).json()) == 2
        r = client.post("/assignments/sync", headers=AUTH, json={"classroom": "nope"})
        assert r.status_code == 404
