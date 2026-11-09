import json
import shutil
import sys
from pathlib import Path

import pytest

from grader import engine

FIXTURES = Path(__file__).parent / "fixtures" / "lab_02"
TEMPLATE = FIXTURES / "template"


def grade(folder: Path, tests=None) -> engine.Result:
    tests = tests or engine.load_tests(TEMPLATE / ".grader" / "tests.json")
    return engine.evaluate(tests, engine.local_runner(folder, sys.executable), folder)


@pytest.fixture
def submission(tmp_path: Path) -> Path:
    for f in TEMPLATE.glob("exercise_*.py"):
        shutil.copy(f, tmp_path)
    return tmp_path


def test_untouched_template_scores_0(submission):
    r = grade(submission)
    assert (r.points, r.max_points) == (0, 9)


def test_exercise_1_solved_scores_3(submission):
    shutil.copy(FIXTURES / "solution" / "exercise_01_phone.py", submission)
    assert grade(submission).points == 3


def test_extra_space_fails_and_is_pointed_out(submission):
    text = (FIXTURES / "solution" / "exercise_01_phone.py").read_text(encoding="utf-8")
    text = text.replace('"Es fijo:              ', '"Es fijo:               ')
    (submission / "exercise_01_phone.py").write_text(text, encoding="utf-8")
    r = grade(submission)
    assert r.points == 0
    explanation = engine.explain_difference(r.tests[0].expected, r.tests[0].output)
    assert "Es·fijo:···············False" in explanation
    assert "column 23" in explanation


def test_complete_solution_scores_9(submission):
    shutil.copytree(FIXTURES / "solution", submission, dirs_exist_ok=True)
    assert grade(submission).points == 9


def _tests(tmp_path: Path, specs: list) -> list:
    path = tmp_path / "t.json"
    path.write_text(json.dumps(specs), encoding="utf-8")
    return engine.load_tests(path)


def test_exact_regex_run_and_setup(tmp_path):
    (tmp_path / "hello.py").write_text("print('  Hello, ' + input() + '  ')\n", encoding="utf-8")
    tests = _tests(tmp_path, [
        {"name": "exact", "type": "io", "run": "python hello.py", "input": "Ana\n",
         "expected": "Hello, Ana", "comparison": "exact", "points": 1},
        {"name": "regex", "type": "io", "run": "python hello.py", "input": "Eva\n",
         "expected": r"^\s*Hello,\s+Eva\b", "comparison": "regex", "points": 2},
        {"name": "run", "type": "run", "run": "python -c 'import sys; sys.exit(3)'",
         "exit-code": 3, "points": 1},
        {"name": "setup fails", "type": "io", "setup": "python -c 'raise SystemExit(1)'",
         "run": "python hello.py", "input": "x\n", "expected": "Hello", "comparison": "included",
         "points": 1},
    ])
    r = grade(tmp_path, tests)
    assert [t.passed for t in r.tests] == [True, True, True, False]
    assert "setup" in r.tests[3].reason
    assert (r.points, r.max_points) == (4, 5)


def test_program_waiting_for_more_input_times_out(tmp_path):
    (tmp_path / "loop.py").write_text("while True:\n    input()\n", encoding="utf-8")
    tests = _tests(tmp_path, [{"name": "t", "type": "io", "run": "python loop.py",
                               "input": "1\n", "expected": "x", "comparison": "included",
                               "timeout": 1, "points": 1}])
    r = grade(tmp_path, tests)
    assert not r.tests[0].passed
    # With the input exhausted input() raises EOFError: the reason reports the error, not a timeout.
    assert "error" in r.tests[0].reason or "took" in r.tests[0].reason


def test_infinite_loop_times_out(tmp_path):
    (tmp_path / "loop.py").write_text("while True:\n    pass\n", encoding="utf-8")
    tests = _tests(tmp_path, [{"name": "t", "type": "run", "run": "python loop.py",
                               "timeout": 1, "points": 1}])
    r = grade(tmp_path, tests)
    assert "took more than 1 s" in r.tests[0].reason


def test_python_splits_points(tmp_path):
    pytest.importorskip("pytest")
    (tmp_path / "test_x.py").write_text(
        "def test_a(): assert True\ndef test_b(): assert True\ndef test_c(): assert False\n"
        "def test_d(): assert True\n", encoding="utf-8")
    tests = _tests(tmp_path, [{"name": "pytest", "type": "python",
                               "run": f"{sys.executable} -m pytest -q", "points": 8}])
    r = grade(tmp_path, tests)
    assert r.tests[0].points == 6 and not r.tests[0].passed
    assert "3/4" in r.tests[0].reason


def test_invalid_tests(tmp_path):
    with pytest.raises(engine.TestFileError):
        _tests(tmp_path, [{"name": "a", "type": "io", "run": "x", "comparison": "similar"}])
    with pytest.raises(engine.TestFileError):
        _tests(tmp_path, [{"name": "a", "type": "run", "run": "x"},
                          {"name": "a", "type": "run", "run": "y"}])
    with pytest.raises(engine.TestFileError):
        _tests(tmp_path, [{"name": "a", "type": "io", "run": "x", "comparison": "exact",
                           "expected-file": "../../etc/passwd"}])
