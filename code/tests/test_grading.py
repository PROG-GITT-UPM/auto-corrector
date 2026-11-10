"""The full cycle against a fake GitHub: list submissions, grade, publish the Release and save the
score in scores.json as the Classroom 50 web would."""
import json
import os
from pathlib import Path

import pytest

from src.modules import publish
from src.modules.grading import grade_submission

from conftest import SOLUTION, template_files

SCHEMAS = os.environ.get("CLASSROOM50_SCHEMAS")  # schemas/ folder of a classroom50 clone


def test_full_cycle(env):
    gh, ctx, assignment = env
    users = [u for u, _, _ in ctx.repos.list_repos(assignment)]
    assert sorted(users) == ["ana", "luis"]             # pepe belongs to lab-2-b

    r_ana = grade_submission(ctx, assignment, "ana")
    r_luis = grade_submission(ctx, assignment, "luis")
    assert (r_ana["points"], r_ana["max_points"]) == (9, 9)
    assert r_luis["points"] == 0                         # his tweaked tests do not count
    assert r_ana["late"] is False

    # Release in the student's repository, on the graded commit, with result.json attached.
    [rel] = gh.releases["grupo-test-lab-2-ana"]
    assert rel["tag_name"].startswith("grading/") and rel["name"].startswith("Grading: 9/9")
    assert "All tests passed" in rel["body"]
    result = json.loads(gh.assets[rel["id"]]["result.json"])
    assert result["schema"] == "classroom50/result/v1" and result["owner"] == "ana"
    assert result["release"] == rel["html_url"]
    [rel_luis] = gh.releases["grupo-test-lab-2-luis"]
    assert "❌" in rel_luis["body"] and "What failed" in rel_luis["body"]

    # Scores are written together, in a single commit with [skip ci].
    assert gh.content_commits == []
    statuses = ctx.scores.save()
    assert set(statuses.values()) == {"new"}
    assert len(gh.content_commits) == 1 and "[skip ci]" in gh.content_commits[0]
    scores = json.loads(gh.read("classroom50", "grupo-test/scores.json"))
    assert "factorial-test" in scores["assignments"]     # what was there is kept
    entries = {e["owner"]: e for e in scores["assignments"]["lab-2"]["entries"]}
    assert entries["ana"]["override"] is True
    record = entries["ana"]["submissions"][0]
    assert record["score"] == 9 and record["submission"].startswith("submit/manual-grader-")
    assert "assignment" not in record and record["late"] is False

    if SCHEMAS:
        jsonschema = pytest.importorskip("jsonschema")
        schemas = Path(SCHEMAS)
        jsonschema.validate(result, json.loads((schemas / "result-v1.schema.json").read_text()))
        jsonschema.validate(scores, json.loads((schemas / "scores-v1.schema.json").read_text()))


def test_does_not_repeat_what_is_already_graded(env):
    gh, ctx, assignment = env
    grade_submission(ctx, assignment, "ana")
    ctx.scores.save()
    second = grade_submission(ctx, assignment, "ana")
    assert second["status"] == "already graded"
    assert len(gh.releases["grupo-test-lab-2-ana"]) == 1
    assert ctx.scores.pending() == 0


def test_keeps_the_teacher_manual_score(env):
    gh, ctx, assignment = env
    grade_submission(ctx, assignment, "luis")
    ctx.scores.save()
    # The teacher gives him a 5 by hand on the Classroom 50 web.
    scores = json.loads(gh.read("classroom50", "grupo-test/scores.json"))
    entry = scores["assignments"]["lab-2"]["entries"][0]
    entry["submissions"].insert(0, {**entry["submissions"][0], "score": 5,
                                    "submission": "submit/manual-2026-11-06T10-00-00Z"})
    gh.put_file("classroom50", "grupo-test/scores.json", json.dumps(scores))
    # Luis pushes another version, after the deadline.
    gh.commit("grupo-test-lab-2-luis",
              {**template_files(), "exercise_04_palindrome.py":
               (SOLUTION / "exercise_04_palindrome.py").read_bytes()}, "2026-11-07T09:00:00Z")
    r = grade_submission(ctx, assignment, "luis")
    assert r["points"] == 2 and r["late"] is True
    assert ctx.scores.save()[("grupo-test", "lab-2", "luis")] == "kept"
    scores = json.loads(gh.read("classroom50", "grupo-test/scores.json"))
    assert scores["assignments"]["lab-2"]["entries"][0]["submissions"][0]["score"] == 5


def test_until_grades_what_was_submitted_on_time(env):
    gh, ctx, assignment = env
    gh.commit("grupo-test-lab-2-ana", template_files(), "2026-11-08T09:00:00Z")
    late = grade_submission(ctx, assignment, "ana")
    on_time = grade_submission(ctx, assignment, "ana", until="2026-11-05T22:00:00Z")
    assert (late["points"], on_time["points"]) == (0, 9)
    ctx.scores.save()
    scores = json.loads(gh.read("classroom50", "grupo-test/scores.json"))
    [entry] = scores["assignments"]["lab-2"]["entries"]
    assert entry["submissions"][0]["score"] == 9         # the on-time one stays as the score


def test_conflict_on_write_retries(env, monkeypatch):
    gh, ctx, assignment = env
    grade_submission(ctx, assignment, "ana")
    write = ctx.gh.write_file
    calls = []

    def with_race(*a, **k):
        if not calls:   # right before our first commit, someone else writes scores.json
            calls.append(1)
            gh.put_file("classroom50", "grupo-test/scores.json",
                        gh.read("classroom50", "grupo-test/scores.json") + " ")
        return write(*a, **k)

    monkeypatch.setattr(ctx.gh, "write_file", with_race)
    assert ctx.scores.save()[("grupo-test", "lab-2", "ana")] == "new"


def test_apply_score_over_autograded_submission():
    scores = {"schema": "classroom50/scores/v1", "assignments": {"t": {"type": "individual",
              "entries": [{"owner": "Ana", "submissions": [
                  {"submission": "submit/2026-01-01T00-00-00Z-abc", "datetime": "2026-01-01T00:00:00Z",
                   "score": 1}]}]}}}
    record = {"owner": "ana", "submission": publish.SUBMISSION_PREFIX + "x",
              "datetime": "2026-01-02T00:00:00Z", "score": 3, "assignment": "t"}
    assert publish.apply_score(scores, "t", "individual", record) == "updated"
    entry = scores["assignments"]["t"]["entries"][0]
    assert entry["override"] is True and [s["score"] for s in entry["submissions"]] == [3, 1]


def test_scores_from_earlier_versions_are_not_teacher_scores():
    scores = {"assignments": {"t": {"type": "individual", "entries": [{"owner": "ana", "submissions": [
        {"submission": "submit/manual-corrector-2026-01-01T00-00-00Z",
         "datetime": "2026-01-01T00:00:00Z", "score": 1}]}]}}}
    record = {"owner": "ana", "submission": publish.SUBMISSION_PREFIX + "x",
              "datetime": "2026-01-02T00:00:00Z", "score": 3}
    assert publish.apply_score(scores, "t", "individual", record) == "updated"
