"""Command line: `grader <command> --help` for the details of each one.

    grader grade    CLASSROOM/ASSIGNMENT [STUDENT ...] [--all]   grades, publishes and saves scores
    grader serve                                                 service with API and watching
    grader local    ASSIGNMENT SUBMISSION                        grades a folder, without GitHub
    grader validate-assignment ASSIGNMENT --solution DIR         checks a new assignment
    grader sync-engine ASSIGNMENT                                copies engine.py to the assignment
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import __version__, config, engine
from .submissions import Template, prepare_folder, read_settings

log = logging.getLogger("grader")

GRADER_DIR = ".grader"


def _print_result(result: engine.Result, details: bool = True) -> None:
    for r in result.tests:
        print(f"  {'✔' if r.passed else '✘'} {r.name}  ({r.points}/{r.max_points})")
        if not r.passed and details:
            print(f"      {r.reason}")
            if r.type == "io" and r.output:
                for line in engine.explain_difference(r.expected, r.output).splitlines():
                    print(f"      {line}")
    print(f"  Total: {result.points}/{result.max_points}")


# ─── grade ───────────────────────────────────────────────────────────────────

def command_grade(args: argparse.Namespace) -> int:
    from .grading import Context, grade_submission
    cfg = config.load(args.config)
    classroom, slug = args.assignment.split("/", 1)
    assignment = cfg.assignment(classroom, slug)
    ctx = Context(cfg)
    students = list(args.students)
    if args.all:
        students = [u for u, _, _ in ctx.repos.list_repos(assignment)]
    if not students:
        print("Say which students to grade or use --all.", file=sys.stderr)
        return 2
    print(f"Grading {len(students)} submission(s) of {assignment.key}…", file=sys.stderr)
    summaries, failures = [], 0
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(grade_submission, ctx, assignment, u, args.until, args.force,
                               not args.no_publish): u for u in students}
        for future in as_completed(futures):
            user = futures[future]
            try:
                r = future.result()
            except Exception as exc:
                failures += 1
                log.error("%s: %s", user, exc)
                r = {"user": user, "status": "error", "error": str(exc)}
            summaries.append(r)
            if not args.json:
                score = f" {r['points']}/{r['max_points']}" if "points" in r else ""
                late = " (late)" if r.get("late") else ""
                print(f"{user}: {r['status']}{score}{late}  {r.get('release', r.get('error', ''))}")
    if not args.no_publish and ctx.scores.pending():
        statuses = ctx.scores.save()
        kept = [k[2] for k, v in statuses.items() if v == "kept"]
        print(f"Scores saved to Classroom 50: {len(statuses) - len(kept)}", file=sys.stderr)
        if kept:
            print(f"Not changed (they have a manual score from the teacher; use --force): "
                  f"{', '.join(kept)}", file=sys.stderr)
    if args.json:
        print(json.dumps(summaries, indent=1, ensure_ascii=False))
    return 1 if failures else 0


# ─── serve ───────────────────────────────────────────────────────────────────

def command_serve(args: argparse.Namespace) -> int:
    from .grading import Context
    from .service import serve
    serve(Context(config.load(args.config)), args.host, args.port)
    return 0


# ─── local ───────────────────────────────────────────────────────────────────

def _local_template(assignment: Path) -> Template:
    return Template(repo=str(assignment), sha="local", dir=assignment,
                    settings=read_settings(assignment / GRADER_DIR))


def _grade_folder(assignment: Path, submission: Path, docker: str | None) -> engine.Result:
    from .sandbox import DockerSandbox, LocalSandbox
    template = _local_template(assignment)
    with tempfile.TemporaryDirectory(prefix="grader-") as tmp:
        folder = Path(tmp) / "submission"
        shutil.copytree(submission, folder, ignore=shutil.ignore_patterns(".git"))
        prepare_folder(folder, template, GRADER_DIR)
        tests = engine.load_tests(
            folder / GRADER_DIR / template.settings.get("tests", "tests.json"))
        box = DockerSandbox(folder, docker) if docker else LocalSandbox(folder, sys.executable)
        with box:
            return engine.evaluate(tests, box.run, folder)


def command_local(args: argparse.Namespace) -> int:
    result = _grade_folder(args.assignment, args.submission, args.docker)
    if args.json:
        print(json.dumps(result.to_dict(), indent=1, ensure_ascii=False))
    else:
        _print_result(result)
    return 0


# ─── validate-assignment ─────────────────────────────────────────────────────

def command_validate(args: argparse.Namespace) -> int:
    assignment: Path = args.assignment
    errors = 0
    try:
        settings = read_settings(assignment / GRADER_DIR)
        tests = engine.load_tests(assignment / GRADER_DIR / settings.get("tests", "tests.json"))
    except (FileNotFoundError, engine.TestFileError, ValueError) as exc:
        print(f"✘ {exc}")
        return 1
    print(f"✔ {len(tests)} tests, {sum(t.points for t in tests)} points")
    if (assignment / ".github" / "workflows").is_dir():
        print("✘ the assignment has .github/workflows: every push would spend Actions minutes")
        errors += 1
    assignment_engine = assignment / GRADER_DIR / "engine.py"
    current_engine = Path(engine.__file__).read_bytes()
    if not assignment_engine.is_file() or assignment_engine.read_bytes() != current_engine:
        print(f"✘ {GRADER_DIR}/engine.py is not the grader's one: grader sync-engine {assignment}")
        errors += 1

    empty = _grade_folder(assignment, assignment, args.docker)
    print(f"{'✔' if empty.points == 0 else '⚠'} untouched template: "
          f"{empty.points}/{empty.max_points} (should be 0)")
    if args.solution:
        with tempfile.TemporaryDirectory() as tmp:
            merged = Path(tmp) / "solution"
            shutil.copytree(assignment, merged, ignore=shutil.ignore_patterns(".git"))
            shutil.copytree(args.solution, merged, dirs_exist_ok=True)
            complete = _grade_folder(assignment, merged, args.docker)
        ok = complete.points == complete.max_points
        print(f"{'✔' if ok else '✘'} with the solution: {complete.points}/{complete.max_points}")
        if not ok:
            _print_result(complete)
            errors += 1
    return 1 if errors else 0


# ─── sync-engine ─────────────────────────────────────────────────────────────

def command_sync(args: argparse.Namespace) -> int:
    for assignment in args.assignments:
        dest = assignment / GRADER_DIR / "engine.py"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(engine.__file__, dest)
        print(f"copied {dest}")
    return 0


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(prog="grader", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"grader {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("grade", help="grades submissions, publishes the result and saves the score")
    g.add_argument("-c", "--config", type=Path, default=Path("grader.toml"))
    g.add_argument("assignment", help="classroom/assignment, e.g. grupo-test/lab-02-prueba")
    g.add_argument("students", nargs="*", help="GitHub usernames")
    g.add_argument("--all", action="store_true", help="every student who accepted the assignment")
    g.add_argument("--until", help="grades the latest commit before this date (ISO 8601)")
    g.add_argument("--force", action="store_true",
                   help="grades even if already graded and overwrites manual scores")
    g.add_argument("--no-publish", action="store_true",
                   help="only grades: no Release and no score (for testing)")
    g.add_argument("--parallel", type=int, default=2, help="grading runs at a time (2)")
    g.add_argument("--json", action="store_true")
    g.set_defaults(func=command_grade)

    s = sub.add_parser("serve", help="service with HTTP API, queue and push watching")
    s.add_argument("-c", "--config", type=Path, default=Path("grader.toml"))
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(func=command_serve)

    lo = sub.add_parser("local", help="grades a folder with the tests of an assignment")
    lo.add_argument("assignment", type=Path, help=f"assignment folder (with {GRADER_DIR}/)")
    lo.add_argument("submission", type=Path, help="folder with the code to grade")
    lo.add_argument("--docker", metavar="IMAGE", help="run in a container with this image")
    lo.add_argument("--json", action="store_true")
    lo.set_defaults(func=command_local)

    v = sub.add_parser("validate-assignment", help="checks an assignment before publishing it")
    v.add_argument("assignment", type=Path)
    v.add_argument("--solution", type=Path, help="folder with the reference solution")
    v.add_argument("--docker", metavar="IMAGE")
    v.set_defaults(func=command_validate)

    m = sub.add_parser("sync-engine", help=f"copies engine.py to {GRADER_DIR}/ of the assignments")
    m.add_argument("assignments", type=Path, nargs="+")
    m.set_defaults(func=command_sync)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except config.ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
