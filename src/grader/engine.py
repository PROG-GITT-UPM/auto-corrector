"""Test engine: runs the tests of an assignment and scores them.

This file is self-contained (standard library only, Python 3.9+) because it is used in two places:

* in the grader, which runs it inside an isolated container on our servers;
* in the repository of each assignment (`.grader/engine.py`), where `check.py` uses it so that
  students can check their exercises on their own computer before submitting.

This way the student and the grader judge with the same code. If you change this file, copy the
new version to the assignments with `grader sync-engine <assignment-folder>`.

Tests follow the Classroom 50 format (the array accepted by `gh teacher assignment add --tests`),
and their semantics are the same as those of the Classroom 50 built-in autograder (`runner.py`):

* `io`: runs `run` with `input` on standard input and compares standard output with `expected`
  according to `comparison`: `included` (literal substring), `exact` (equal except for leading and
  trailing whitespace) or `regex` (multiline `re.search`).
* `run`: passes if the exit code of `run` is `exit-code` (0 if not given).
* `python`: runs pytest and splits the points among the cases that pass.

`setup` runs before `run`; if it fails, the test fails. `timeout` applies to each command
separately (10 s if not given).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

ENGINE_VERSION = "1.0.0"
DEFAULT_TIMEOUT = 10
MAX_CHARS = 2000
COMPARISONS = ("included", "exact", "regex")
TYPES = ("io", "run", "python")
PYTEST_REPORT = ".grader-pytest.xml"


class TestFileError(ValueError):
    """The test file is not valid."""
    __test__ = False  # not a pytest test class


@dataclass
class TestSpec:
    __test__ = False  # not a pytest test class
    name: str
    type: str
    run: str
    points: int = 0
    setup: str = ""
    input: str = ""
    expected: str = ""
    comparison: str = "included"
    timeout: int = DEFAULT_TIMEOUT
    exit_code: int = 0


@dataclass
class Execution:
    """What a runner returns after launching a command."""
    code: Optional[int]
    output: str
    error: str = ""
    timed_out: bool = False


# A runner launches a shell command in the submission folder: (command, input, timeout).
Runner = Callable[[str, Optional[str], int], Execution]


@dataclass
class TestResult:
    __test__ = False  # not a pytest test class
    name: str
    type: str
    passed: bool
    points: int
    max_points: int
    reason: str = ""
    run: str = ""
    input: str = ""
    expected: str = ""
    output: str = ""
    error: str = ""
    comparison: str = ""

    def to_classroom50(self) -> dict:
        """A row of `tests` in result.json (classroom50/result/v1); extra fields are kept."""
        row: dict = {
            "test-name": self.name,
            "passed": self.passed,
            "score": self.points,
            "max-score": self.max_points,
        }
        if not self.passed and self.reason:
            row["detail"] = self.reason
        return row


@dataclass
class Result:
    tests: list = field(default_factory=list)

    @property
    def points(self) -> int:
        return sum(r.points for r in self.tests)

    @property
    def max_points(self) -> int:
        return sum(r.max_points for r in self.tests)

    def to_dict(self) -> dict:
        return {"points": self.points, "max_points": self.max_points,
                "tests": [asdict(r) for r in self.tests]}


# ─── Loading tests ─────────────────────────────────────────────────────────────

def _text_or_file(spec: dict, key: str, base: Path) -> str:
    """The value of `key` or the contents of `key-file` (relative to the test file)."""
    if spec.get(key) is not None and spec.get(f"{key}-file") is not None:
        raise TestFileError(f"'{spec['name']}': {key} and {key}-file cannot be used together")
    if spec.get(f"{key}-file") is not None:
        path = (base / spec[f"{key}-file"]).resolve()
        if base.resolve() not in path.parents and path != base.resolve():
            raise TestFileError(f"'{spec['name']}': {key}-file is outside the tests folder")
        if not path.is_file():
            raise TestFileError(f"'{spec['name']}': {spec[f'{key}-file']} does not exist")
        return normalize(path.read_text(encoding="utf-8"))
    return spec.get(key) or ""


def parse_tests(data: object, base: Path) -> list:
    """Validates an array of Classroom 50 tests (or an object with `tests`) and converts it."""
    if isinstance(data, dict) and "tests" in data:
        data = data["tests"]
    if not isinstance(data, list):
        raise TestFileError("the test file must be an array of tests")
    tests: list = []
    names: set = set()
    for i, spec in enumerate(data, start=1):
        if not isinstance(spec, dict):
            raise TestFileError(f"test {i} is not an object")
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            raise TestFileError(f"test {i} has no name")
        if name in names:
            raise TestFileError(f"there are two tests named '{name}'")
        names.add(name)
        kind = spec.get("type")
        if kind not in TYPES:
            raise TestFileError(f"'{name}': type must be one of {TYPES}")
        if not isinstance(spec.get("run"), str) or not spec["run"].strip():
            raise TestFileError(f"'{name}': run is missing")
        points = spec.get("points", 0)
        if isinstance(points, bool) or not isinstance(points, int) or points < 0:
            raise TestFileError(f"'{name}': points must be a non-negative integer")
        timeout = spec.get("timeout", 0)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 <= timeout <= 600:
            raise TestFileError(f"'{name}': timeout must be between 0 and 600")
        comparison = spec.get("comparison", "included")
        if kind == "io" and comparison not in COMPARISONS:
            raise TestFileError(f"'{name}': comparison must be one of {COMPARISONS}")
        tests.append(TestSpec(
            name=name, type=kind, run=spec["run"], points=points,
            setup=spec.get("setup") or "",
            input=_text_or_file(spec, "input", base),
            expected=_text_or_file(spec, "expected", base),
            comparison=comparison,
            timeout=timeout or DEFAULT_TIMEOUT,
            exit_code=int(spec.get("exit-code") or 0),
        ))
    return tests


def load_tests(path: Path) -> list:
    """Reads a test file (JSON). `*-file` paths are relative to its folder."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TestFileError(f"{path}: invalid JSON ({exc})") from exc
    return parse_tests(data, Path(path).parent)


# ─── Comparison ────────────────────────────────────────────────────────────────

def normalize(text: Optional[str]) -> str:
    """Windows or old Mac line endings → \\n, as Classroom 50 does."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def compare(output: str, expected: str, comparison: str) -> bool:
    if comparison == "included":
        return expected in output
    if comparison == "exact":
        return output.strip() == expected.strip()
    if comparison == "regex":
        return re.search(expected, output, re.MULTILINE) is not None
    raise ValueError(f"unknown comparison: {comparison}")


def truncate(text: str, limit: int = MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


def _visible(line: str) -> str:
    """Makes spaces and tabs visible so that it is clear where a line differs."""
    return line.replace(" ", "·").replace("\t", "→")


def explain_difference(expected: str, output: str) -> str:
    """Where the output differs from what was expected, line by line, with whitespace visible.

    Looks for the chunk of the output that most resembles the expected text (the output usually
    starts with the `input()` prompts, which are not in the expected text) and points out the first
    line that differs and the first column where it differs."""
    expected_lines = expected.strip("\n").split("\n")
    output_lines = output.split("\n")
    if not expected_lines or not expected.strip():
        return ""
    # The window of the output, the same size as the expected text, that most resembles it.
    best, best_start = -1.0, 0
    size = len(expected_lines)
    for start in range(max(1, len(output_lines) - size + 1)):
        window = output_lines[start:start + size]
        similarity = difflib.SequenceMatcher(None, "\n".join(expected_lines),
                                             "\n".join(window)).ratio()
        if similarity > best:
            best, best_start = similarity, start
    window = output_lines[best_start:best_start + size]
    for n, expected_line in enumerate(expected_lines):
        output_line = window[n] if n < len(window) else None
        if output_line is None:
            return (f"Line {n + 1} of the expected output is missing:\n"
                    f"  expected: {_visible(expected_line)}\n  your output ends earlier.")
        if output_line != expected_line:
            col = next((i for i, (a, b) in enumerate(zip(expected_line, output_line)) if a != b),
                       min(len(expected_line), len(output_line)))
            return ("First line that differs (· is a space):\n"
                    f"  expected:    {_visible(expected_line)}\n"
                    f"  your output: {_visible(output_line)}\n"
                    f"               {' ' * col}^ differs at column {col + 1}")
    return "The lines match, but not one after another or with extra lines between them."


# ─── Execution ─────────────────────────────────────────────────────────────────

def _evaluate_python(test: TestSpec, runner: Runner, directory: Path) -> TestResult:
    """pytest with a JUnit report (built into pytest, no plugins) and points split by cases."""
    report = directory / PYTEST_REPORT
    if report.exists():
        report.unlink()
    command = test.run
    if "--junitxml" not in command:
        command = f"{command} -p no:cacheprovider --junitxml={shlex.quote(PYTEST_REPORT)}"
    ex = runner(command, None, test.timeout)
    base = dict(name=test.name, type="python", max_points=test.points, run=test.run,
                output=normalize(ex.output), error=normalize(ex.error))
    if ex.timed_out:
        return TestResult(passed=False, points=0,
                          reason=f"took more than {test.timeout} s", **base)
    total = passed_cases = 0
    if report.is_file():
        try:
            root = ET.parse(report).getroot()
            for suite in ([root] if root.tag == "testsuite" else root.iter("testsuite")):
                n = int(suite.get("tests", 0))
                bad = sum(int(suite.get(k, 0)) for k in ("failures", "errors", "skipped"))
                total += n
                passed_cases += n - bad
        except (ET.ParseError, ValueError):
            total = 0
        report.unlink(missing_ok=True)
    if total:
        passed = passed_cases == total
        points = max(0, min(test.points, round(test.points * passed_cases / total)))
        if not passed:
            points = min(points, max(0, test.points - 1))
        return TestResult(passed=passed, points=points,
                          reason=f"pytest: {passed_cases}/{total} cases passed", **base)
    passed = ex.code == 0
    return TestResult(passed=passed, points=test.points if passed else 0,
                      reason=f"pytest exited with code {ex.code} (no report)", **base)


def evaluate_test(test: TestSpec, runner: Runner, directory: Path) -> TestResult:
    base = dict(name=test.name, type=test.type, max_points=test.points, run=test.run)
    if test.setup:
        ex = runner(test.setup, None, test.timeout)
        if ex.timed_out or ex.code != 0:
            reason = (f"setup took more than {test.timeout} s" if ex.timed_out
                      else f"setup ('{test.setup}') exited with code {ex.code}")
            return TestResult(passed=False, points=0, reason=reason,
                              output=normalize(ex.output), error=normalize(ex.error), **base)
    if test.type == "python":
        return _evaluate_python(test, runner, directory)

    ex = runner(test.run, test.input if test.type == "io" else None, test.timeout)
    output, error = normalize(ex.output), normalize(ex.error)
    if ex.timed_out:
        return TestResult(passed=False, points=0, input=test.input, output=output,
                          error=error, expected=test.expected, comparison=test.comparison,
                          reason=(f"took more than {test.timeout} s: is it waiting for more input "
                                  "than it is given, or does it have a loop that never ends?"),
                          **base)
    if test.type == "run":
        passed = ex.code == test.exit_code
        return TestResult(passed=passed, points=test.points if passed else 0,
                          output=output, error=error,
                          reason=f"exit code {ex.code} (expected {test.exit_code})", **base)
    try:
        passed = compare(output, test.expected, test.comparison)
    except re.error as exc:
        passed, reason = False, f"invalid regular expression in the test: {exc}"
    else:
        if passed:
            reason = ""
        elif ex.code not in (0, None) and error.strip():
            last = error.strip().splitlines()[-1]
            reason = f"the program ended with an error: {last}"
        else:
            reason = "the output does not contain the expected text"
    return TestResult(passed=passed, points=test.points if passed else 0,
                      reason=reason, input=test.input, expected=test.expected,
                      output=output, error=error, comparison=test.comparison, **base)


def evaluate(tests: list, runner: Runner, directory: Path,
             on_finish: Optional[Callable[[TestResult], None]] = None) -> Result:
    result = Result()
    for test in tests:
        r = evaluate_test(test, runner, Path(directory))
        result.tests.append(r)
        if on_finish:
            on_finish(r)
    return result


def local_runner(directory: Path, python: Optional[str] = None) -> Runner:
    """Runs commands with a subprocess in `directory`. It does NOT isolate anything: only for
    students on their own computer or for the grader's tests. On the server, use the container.

    With `python`, a command that starts with `python` or `python3` uses that interpreter (on
    Windows `python` is sometimes the Microsoft Store shortcut)."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHONDONTWRITEBYTECODE="1")

    def run(command: str, stdin: Optional[str], timeout: int) -> Execution:
        if python:
            command = re.sub(r"^python3?(?=\s|$)", lambda _: shlex.quote(python)
                             if os.name != "nt" else f'"{python}"', command.strip())
        try:
            p = subprocess.run(command, shell=True, cwd=directory, input=stdin or "",
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            def _txt(x: object) -> str:
                return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
            return Execution(None, _txt(exc.stdout), _txt(exc.stderr), timed_out=True)
        return Execution(p.returncode, p.stdout, p.stderr)

    return run
