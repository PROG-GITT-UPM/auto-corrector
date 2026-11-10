"""The smoke-test assignment (template-graded-exercise) graded as the service grades it: the
template's .grader/ and protected files replace the student's, then every test type runs."""
import shutil
import sys
from pathlib import Path

import pytest

from src.modules import engine
from src.modules.submissions import Template, prepare_folder, read_settings

FIXTURE = Path(__file__).parent / "fixtures" / "template_graded_exercise"
TEMPLATE = FIXTURE / "template"
SOLUTION = FIXTURE / "solution"

pytest.importorskip("pytest")  # exercise 3 is graded with pytest


def grade(submission: Path) -> engine.Result:
    template = Template(repo="PROG-GITT-UPM/template-graded-exercise", sha="0" * 40,
                        dir=TEMPLATE, settings=read_settings(TEMPLATE / ".grader"))
    restored = prepare_folder(submission, template, ".grader")
    assert restored == ["data/numbers.txt"]
    tests = engine.load_tests(submission / ".grader" / template.settings["tests"])
    return engine.evaluate(tests, engine.local_runner(submission, sys.executable), submission)


@pytest.fixture
def submission(tmp_path: Path) -> Path:
    folder = tmp_path / "submission"
    shutil.copytree(TEMPLATE, folder)
    return folder


def test_untouched_template_scores_0(submission):
    result = grade(submission)
    assert (result.points, result.max_points) == (0, 7)
    assert {t.name.split(" · ")[0] for t in result.tests} == {
        "Ejercicio 1 — Saludo", "Ejercicio 2 — Media de data/numbers.txt",
        "Ejercicio 3 — Temperatura"}


def test_reference_solution_scores_7(submission):
    shutil.copytree(SOLUTION, submission, dirs_exist_ok=True)
    result = grade(submission)
    assert (result.points, result.max_points) == (7, 7), [
        (t.name, t.reason) for t in result.tests if not t.passed]


def test_changing_the_protected_data_does_not_help(submission):
    shutil.copytree(SOLUTION, submission, dirs_exist_ok=True)
    (submission / "data" / "numbers.txt").write_text("1\n", encoding="utf-8")
    assert grade(submission).points == 7          # the original numbers are put back


def test_pytest_points_are_split_by_case(submission):
    shutil.copytree(SOLUTION, submission, dirs_exist_ok=True)
    # Wrong for 0 and 100, right by chance for -40: 1 of 3 cases.
    (submission / "exercise_03_temperature.py").write_text(
        "def celsius_to_fahrenheit(celsius: float) -> float:\n    return celsius\n",
        encoding="utf-8")
    [conversions] = [t for t in grade(submission).tests if t.name.endswith("conversiones")]
    assert (conversions.points, conversions.max_points) == (1, 3) and not conversions.passed
