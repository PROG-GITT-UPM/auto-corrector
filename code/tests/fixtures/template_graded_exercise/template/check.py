"""Check your exercises against the examples in the assignment sheet before submitting.

    python check.py        all tests
    python check.py 2      only the tests for exercise 2

In VS Code you can also use Terminal → Run Task… → "Comprobar mis ejercicios".

These are the same tests the course grader runs when you submit.
Do not change anything in the .grader folder: the grader always uses the original version.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".grader"))

import engine  # noqa: E402

GREEN, RED, GREY, RESET = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = GREY = RESET = ""


def read_config() -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python older than 3.11
        return {"tests": "tests.json"}
    with open(ROOT / ".grader" / "assignment.toml", "rb") as f:
        return tomllib.load(f).get("assignment", {})


def select(tests: list, pattern: str) -> list:
    if not pattern:
        return tests
    if pattern.isdigit():
        n = int(pattern)
        keys = (f"exercise_{n:02d}", f"exercise_{n}_", f"exercise_{n}.", f"exercise {n} ")
        return [t for t in tests if any(k in t.run.lower() or k in t.name.lower() + " "
                                        for k in keys)]
    pattern = pattern.lower()
    return [t for t in tests if pattern in t.name.lower() or pattern in t.run.lower()]


def show(r: "engine.TestResult") -> None:
    if r.passed:
        print(f"{GREEN}✔{RESET} {r.name}")
        return
    print(f"{RED}✘ {r.name}{RESET}: {r.reason}")
    if r.type == "io":
        if r.input:
            print(f"{GREY}  Typed on the keyboard:{RESET}")
            for line in r.input.rstrip("\n").split("\n"):
                print(f"    {line}")
        difference = engine.explain_difference(r.expected, r.output) if r.output else ""
        if difference:
            for line in difference.split("\n"):
                print(f"  {line}")
        else:
            print(f"{GREY}  Expected output:{RESET}")
            for line in r.expected.split("\n"):
                print(f"    {line}")
    if r.error.strip():
        print(f"{GREY}  Program error:{RESET}")
        for line in r.error.strip().split("\n")[-6:]:
            print(f"    {line}")
    print()


def main() -> int:
    config = read_config()
    tests = engine.load_tests(ROOT / ".grader" / config.get("tests", "tests.json"))
    selected = select(tests, sys.argv[1] if len(sys.argv) > 1 else "")
    if not selected:
        print(f'No test matches "{sys.argv[1]}".')
        return 2
    result = engine.evaluate(selected, engine.local_runner(ROOT, sys.executable), ROOT,
                             on_finish=show)
    total = f"{result.points} of {result.max_points} points"
    if result.points == result.max_points:
        print(f"\n{GREEN}All good: {total}.{RESET} Commit and push to submit.")
    else:
        print(f"\n{total}. Fix what fails and check again.")
    return 0 if result.points == result.max_points else 1


if __name__ == "__main__":
    sys.exit(main())
