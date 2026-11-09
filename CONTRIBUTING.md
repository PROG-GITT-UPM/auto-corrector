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

`src/grader/engine.py` is copied into every assignment as `.grader/engine.py`, so that students run
exactly the same code with `check.py`. It must be byte-for-byte identical everywhere.

- Change it only here, never in an assignment repository.
- Keep it self-contained: standard library only, and Python 3.9 compatible (students may have an
  older Python than the server).
- After changing it, bump `ENGINE_VERSION`, run the tests, and copy it to the template and to every
  assignment that is still open:

  ```bash
  grader sync-engine ../template-graded-exercise ../prog-lab-2 ...
  ```

  `grader validate-assignment` fails if an assignment has a different engine.
- Do not rename its public names (`load_tests`, `evaluate`, `local_runner`, `explain_difference`,
  `TestResult`…): the `check.py` of every assignment already accepted by students uses them, and
  their copy is not updated when you sync.

## Code

- Standard library only for the grader too. The server should only need Python and Docker.
- Classes whose name starts with `Test` but are not tests carry `__test__ = False`, so pytest does
  not try to collect them.
- Never run student code outside the sandbox. `LocalSandbox` exists only for development and
  tests, behind `insecure = true`.
- Anything that writes to the `classroom50` repository must put `[skip ci]` in the commit message
  and batch writes, or it will spend Actions minutes on `publish-pages`.

## Tests

```bash
pip install -e ".[dev]"
pytest
CLASSROOM50_SCHEMAS=~/classroom50/schemas pytest   # also validates against Classroom 50's schemas
```

All tests must pass before merging. They need neither network nor Docker.

`tests/fixtures/lab_02/` holds a copy of the lab 2 template and its reference solution. The
solution is why this repository must stay private.

## Commits

English, in the conventional style already used in the history (`feat: …`, `fix: …`, `docs: …`).
