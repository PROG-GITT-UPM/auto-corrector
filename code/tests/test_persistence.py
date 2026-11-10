"""What the database adds: hidden tests, and resuming where the service stopped."""
import json

from src.modules.grading import Context, grade_submission
from src.modules.service import GradingService
from src.providers.database import Database

HIDDEN = [
    {"name": "Ejercicio 2 — Letra del DNI · caso oculto 1", "type": "io",
     "run": "python exercise_02_dni.py", "input": "00000000\n",
     "expected": "Letra:              T", "comparison": "included", "points": 1},
    {"name": "Ejercicio 2 — Letra del DNI · caso oculto 2", "type": "io",
     "run": "python exercise_02_dni.py", "input": "00000001\n",
     "expected": "SECRET-EXPECTED-TEXT", "comparison": "included", "points": 2},
]


def test_hidden_tests_count_but_are_not_shown(env):
    gh, ctx, assignment = env
    assignment = ctx.db.set_hidden_tests("grupo-test", "lab-2", HIDDEN)
    r = grade_submission(ctx, assignment, "ana")
    names = [t["name"] for t in r["tests"]]
    assert names[-2:] == [t["name"] for t in HIDDEN] and r["max_points"] == 12
    [release] = gh.releases["grupo-test-lab-2-ana"]
    assert "caso oculto 2" in release["body"]                  # students see it failed...
    assert "SECRET-EXPECTED-TEXT" not in release["body"]       # ...but not what was expected
    assert "00000001" not in release["body"]
    result = json.loads(gh.assets[release["id"]]["result.json"])
    assert "SECRET" not in json.dumps(result)


def test_changing_hidden_tests_grades_again(env):
    gh, ctx, assignment = env
    assert grade_submission(ctx, assignment, "ana")["status"] == "graded"
    assert grade_submission(ctx, assignment, "ana")["status"] == "already graded"
    assignment = ctx.db.set_hidden_tests("grupo-test", "lab-2", HIDDEN[:1])
    assert grade_submission(ctx, assignment, "ana")["status"] == "graded"


def test_pending_scores_survive_a_restart(env, settings):
    gh, ctx, assignment = env
    grade_submission(ctx, assignment, "ana")
    ctx.db.close()                                # the service stops before saving
    db = Database(settings.database_url)          # ...and starts again
    restarted = Context(settings, db)
    assert restarted.scores.pending() == 1
    assert restarted.scores.save() == {("grupo-test", "lab-2", "ana"): "new"}
    assert restarted.scores.pending() == 0
    db.close()


def test_interrupted_jobs_are_queued_again(env, settings):
    gh, ctx, assignment = env
    ident = ctx.db.enqueue("grupo-test", "lab-2", "ana")
    assert ctx.db.claim_job()["id"] == ident      # a worker takes it...
    ctx.db.close()                                # ...and the service dies half-way
    db = Database(settings.database_url)
    assert db.requeue_interrupted() == 1
    assert db.get_job(ident)["status"] == "queued"
    db.close()


def test_one_queued_job_per_student(env):
    gh, ctx, assignment = env
    service = GradingService(ctx)
    assert service.enqueue(assignment, "ana")
    assert service.enqueue(assignment, "ANA") is None     # same student, already waiting
    assert service.enqueue(assignment, "luis")
