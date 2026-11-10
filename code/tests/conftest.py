"""Shared fixtures: a fake GitHub with the lab 2 setup and a clean PostgreSQL database.

The tests need a PostgreSQL server: set TEST_DATABASE_URL (the database is wiped on every test).
With Docker: docker run -d --name grader-test-db -p 5433:5432 -e POSTGRES_PASSWORD=test postgres:16
and TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres
"""
import json
import os
from pathlib import Path

import psycopg
import pytest

from src.config.settings import Settings
from src.modules.grading import Context
from src.providers.database import Database

from fake_github import FakeGitHub

ORG = "PROG-GITT-UPM"
FIXTURES = Path(__file__).parent / "fixtures" / "lab_02"
TEMPLATE = FIXTURES / "template"
SOLUTION = FIXTURES / "solution"
API_TOKEN = "test-api-token"


def template_files() -> dict:
    return {p.relative_to(TEMPLATE).as_posix(): p.read_bytes()
            for p in TEMPLATE.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


@pytest.fixture
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


@pytest.fixture
def fake_github():
    gh = FakeGitHub(ORG)
    template = template_files()
    gh.commit("prog-lab-2", template, "2026-11-01T10:00:00Z")
    gh.put_file("classroom50", "grupo-test/assignments.json", json.dumps({
        "schema": "classroom50/assignments/v1",
        "assignments": [
            {"slug": "lab-2", "name": "Lab 2", "mode": "individual",
             "template": {"owner": ORG, "repo": "prog-lab-2", "branch": "main"},
             "autograder": "default", "no_autograder": True,
             "grading": {"mode": "manual", "max_points": 9}, "due": "2026-11-05T22:00:00Z"},
            {"slug": "lab-2-b", "name": "Another", "mode": "individual",
             "autograder": "default"},
        ]}))
    gh.put_file("classroom50", "grupo-test/scores.json", json.dumps({
        "schema": "classroom50/scores/v1",
        "assignments": {"factorial-test": {"type": "individual", "entries": [
            {"owner": "hugo", "submissions": [{
                "schema": "classroom50/result/v1", "classroom": "grupo-test",
                "assignment_type": "individual", "owner": "hugo",
                "submission": "submit/2026-10-12T10-00-00Z-abcdef1", "commit": "c", "release": "r",
                "review": "v", "datetime": "2026-10-12T10:00:00Z", "score": 1, "max-score": 1,
                "tests": []}]}]}}}))
    # ana: everything solved. luis: untouched template and some "tweaked" tests that always pass.
    # pepe: belongs to another assignment whose name starts the same way.
    ana = {**template, **{p.name: p.read_bytes() for p in SOLUTION.iterdir()}}
    gh.commit("grupo-test-lab-2-ana", ana, "2026-11-03T18:00:00Z")
    cheat = {**template, ".grader/tests.json": json.dumps(
        [{"name": "always", "type": "run", "run": "true", "points": 9}]).encode()}
    gh.commit("grupo-test-lab-2-luis", cheat, "2026-11-04T09:00:00Z")
    gh.commit("grupo-test-lab-2-b-pepe", template, "2026-11-04T10:00:00Z")
    yield gh
    gh.close()


@pytest.fixture
def settings(fake_github, database_url, tmp_path) -> Settings:
    return Settings(organization=ORG, github_token="test-token", api_token=API_TOKEN,
                    database_url=database_url, github_api=fake_github.url,
                    work_dir=tmp_path / "work", workers=2, save_every=3600, watch_tick=1,
                    sandbox={"type": "local", "insecure": True})


@pytest.fixture
def env(fake_github, settings):
    """(fake GitHub, grading context, the lab-2 assignment saved in the database)."""
    db = Database(settings.database_url)
    db.migrate()
    ctx = Context(settings, db)
    assignment = db.upsert_assignment("grupo-test", "lab-2", f"{ORG}/prog-lab-2", "main",
                                      True, True)
    yield fake_github, ctx, assignment
    db.close()
