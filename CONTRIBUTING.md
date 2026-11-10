# Contributing to auto-grader

This repository is for teachers only. Everything in it is in English.

The course has three kinds of repositories, and they have to stay consistent with each other:

| Repository | Audience | What it is |
|---|---|---|
| `auto-grader` | Teachers | The grader that runs on our servers (this repository). Private. |
| `template-graded-exercise` | Teachers | The starting point for every new assignment. |
| `prog-lab-N`, `prog-pN`… | Students | One per assignment, created from the template and used as the Classroom 50 template. |

## Language

The rule is the same in all three kinds of repository: **Spanish for the assignment statement
that students read; English for everything else.**

| Spanish (assignment statement) | English (everything else) |
|---|---|
| The student `README.md` of an assignment | All code, identifiers, comments and docstrings |
| The header comment of each exercise file | All teacher documentation (`README.md` and `CONTRIBUTING.md` here; `.grader/TEACHERS.md` in assignments) |
| Test `name`s in `tests.json` (they quote the sheet: «Ejercicio 1 — … · ejemplo 1») | Messages printed by the grader and by `check.py`, and the Release text |
| `.grader/README.md` (the note telling students not to touch the folder) | Configuration files and their comments |
| The VS Code task label («Comprobar mis ejercicios») | Commit messages, issues and pull requests |

Two consequences that are easy to miss:

- **Exercise file names are part of the statement.** They must match exactly what the sheet
  tells students to create (the `ficheros:` list in the sheet's front matter, which the test
  generator also uses for the `run` commands). If you rename one, rename it in the sheet too.
- **Data identifiers stay as they are.** Classroom 50 slugs and organization names
  (`grupo-test`, `lab-2`) are data, not prose: do not translate them.

## The engine is shared

`code/src/modules/engine.py` is copied into every assignment as `.grader/engine.py`, so that
students run exactly the same code with `check.py`. It must be byte-for-byte identical everywhere.

- Change it only here, never in an assignment repository.
- Keep it self-contained: standard library only, and Python 3.9 compatible (students may have an
  older Python than the server).
- After changing it, bump `ENGINE_VERSION`, run the tests, and copy it to the template and to every
  assignment that is still open:

  ```bash
  cp code/src/modules/engine.py ../template-graded-exercise/.grader/engine.py
  cp code/src/modules/engine.py ../prog-lab-2/.grader/engine.py
  ```

- Do not rename its public names (`load_tests`, `evaluate`, `local_runner`, `explain_difference`,
  `TestResult`…): the `check.py` of every assignment already accepted by students uses them, and
  their copy is not updated when you change it here.

## Code

The layout follows the STRAST FastAPI template:

| Folder | What goes there |
|---|---|
| `code/main.py` | The FastAPI app: routers, and the start-up and shutdown of the background service. |
| `code/src/routers/` | One router per group of endpoints. Endpoint paths and tags are in `utilities/endpoints_tags.py`. |
| `code/src/data_models/` | Pydantic models of requests and responses. Their descriptions and examples are the API docs: write them for someone trying the API in `/docs`. |
| `code/src/modules/` | The grading logic: engine, submissions, reports, publishing scores, the background service. |
| `code/src/providers/` | Access to the outside: PostgreSQL, the GitHub API and Docker. |
| `code/src/config/` | Settings from environment variables. |
| `code/src/utilities/` | Constants, endpoint names and API authentication. |

- **All state goes in PostgreSQL**, never in files or memory: the service must resume where it
  stopped, and moving the database volume must be enough to move the service. The work folder is a
  disposable cache.
- **Schema changes are new migrations.** Add an entry at the end of `MIGRATIONS` in
  `providers/database.py`; never edit one that has been released.
- **One process.** The background workers live in the API process, so the image runs a single
  `fastapi` worker. Grading parallelism is `GRADER_WORKERS`.
- **Endpoints are synchronous functions** (`def`, not `async def`): they call the database and
  GitHub with blocking clients, and FastAPI runs them in its thread pool.
- **Never run student code outside the sandbox.** `LocalSandbox` exists only for development and
  tests, behind `SANDBOX_INSECURE=true`.
- **Hidden tests never reach students.** The Release and `result.json` say whether a hidden test
  passed, never its input, expected output or the student's output.
- Classes whose name starts with `Test` but are not tests carry `__test__ = False`, so pytest does
  not try to collect them.
- Anything that writes to the `classroom50` repository must put `[skip ci]` in the commit message
  and batch writes, or it will spend Actions minutes on `publish-pages`.

## Tests

The tests need a PostgreSQL server; they wipe the database they are given on every test, so use a
throw-away one:

```bash
docker run -d --name grader-test-db -p 5433:5432 -e POSTGRES_PASSWORD=test postgres:16
pip install -r requirements-dev.txt
cd code
TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres pytest
CLASSROOM50_SCHEMAS=~/classroom50/schemas TEST_DATABASE_URL=... pytest   # also checks the schemas
```

Without `TEST_DATABASE_URL`, only the engine tests run. All tests must pass before merging. They
need neither network nor Docker: GitHub is faked (`tests/fake_github.py`) and student code runs
with `LocalSandbox`. The Docker sandbox is not covered by the tests: check it on a real server.

`tests/fixtures/` holds copies of two assignments with their reference solutions (which is why
this repository must stay private):

- `lab_02/`: the `prog-lab-2` template. When it changes, copy its `.grader/tests.json`,
  `.grader/assignment.toml` and exercise files there.
- `template_graded_exercise/`: the whole `template-graded-exercise` repository, which is also the
  smoke-test assignment. When it changes, copy it again:
  `git -C ../template-graded-exercise archive HEAD | tar -x -C code/tests/fixtures/template_graded_exercise/template`.

## Commits

English, in the conventional style already used in the history (`feat: …`, `fix: …`, `docs: …`).
