# auto-grader

Grades the course's Python assignments on our own servers and records the score in Classroom 50
without using GitHub Actions.

GitHub only stores the repositories. Classroom 50 is still the interface: enrollment, accepting
the assignment, the submissions page and the score export. Student code runs here.

```
student ── push ──▶ student repo (no workflows: spends no minutes)
                          │
              grader ◀────┘  (downloads the commit through the API)
               │
               ├─ runs the template's tests in a container without network
               ├─▶ Release "Grading: 7/9" in the student repo (what the student sees)
               └─▶ score in classroom50/<classroom>/scores.json (what Classroom 50 sees)
```

The repository is called `auto-grader`; the Python package and the command are called `grader`.

> **Keep this repository private.** `tests/fixtures/lab_02/solution/` holds the reference
> solutions of lab 2, and any hidden tests you add later would be visible too.

Conventions for working on this repository (language, naming, keeping the engine in sync) are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## How it fits with Classroom 50

The assignment is created in Classroom 50 with two options:

- **Grading: Manual**, with the assignment's points.
- **Do not use the built-in autograder** (`no_autograder`). The student repository is then an exact
  copy of the template, without workflows, and pushes trigger nothing in Actions.

An assignment created without the second option keeps the Classroom 50 autograder, so every push
spends minutes even if this grader also grades it (the grader logs a warning). The pilot's
`lab-02-prueba` was one of those; the examples here use `lab-2`, created with both options.

The score the grader writes is the same one the web writes when a teacher enters a score by hand:
an entry with `"override": true` in `scores.json`, modelled on the web's code
(`web/src/domain/assignments/scoreOverride.ts`). That is why it shows up on the submissions page
and in the "Download scores" CSV, and why the Classroom 50 score collector leaves it alone.

Two details that matter:

- **`[skip ci]` on every score commit.** Every commit to the `classroom50` repository triggers its
  `publish-pages` workflow, which does spend minutes. The grader batches scores into one commit
  per classroom with `[skip ci]` in the message. Note that entering scores by hand on the web
  does trigger that workflow, once per score.
- **Manual scores from teachers.** If a teacher enters a score by hand on the web, the grader does
  not overwrite it when grading again; it logs a warning. `--force` overwrites it.

The grader's scores are recognised by the `submit/manual-grader-` prefix. Scores written by the
first, Spanish version (`submit/manual-corrector-`) are also treated as the grader's own, not as
a teacher's.

Checked against the Classroom 50 code of 5 November 2026 (commit `b6d81a4`). If Classroom 50
changes the format of `scores.json`, review `publish.py`.

## Installing on the server

Requires Linux with Docker and Python 3.11 or later. The grader only uses the standard library.

```bash
sudo useradd --system --home /var/lib/grader --groups docker grader
sudo python3 -m venv /opt/grader/venv
sudo /opt/grader/venv/bin/pip install /path/to/auto-grader
docker build -t grader-sandbox:3.12 sandbox/      # image in which student code runs

sudo mkdir -p /etc/grader
sudo cp grader.example.toml /etc/grader/grader.toml   # then adjust it
sudo install -m 600 -o grader /dev/null /etc/grader/secrets.env
```

Contents of `/etc/grader/secrets.env`:

```
GITHUB_TOKEN=github_pat_...
GRADER_API_TOKEN=a-long-random-string
```

### The GitHub token

A *fine-grained* token with:

- **Resource owner:** the organization.
- **Repositories:** all of them (student repositories are created on the fly).
- **Permissions:**
  - **Contents: Read and write**, to download code, create Releases and write `scores.json`.
  - **Metadata: Read**.

The token expires. Write the date down and renew it before the semester starts.

## Usage

### Batch: grade and exit

```bash
grader grade -c grader.toml grupo-test/lab-2 ana luis
grader grade -c grader.toml grupo-test/lab-2 --all
grader grade -c grader.toml grupo-test/lab-2 --all --until 2026-11-15T22:59:00Z
grader grade -c grader.toml grupo-test/lab-2 ana --no-publish   # just look
```

- **`--until`** grades the latest commit before that date: what was submitted on time.
- **Repeated runs:** a commit already graded with the same tests is not graded again, unless
  `--force` is given. If you change the template's tests, submissions are graded again.

`deploy/grader-deadline.example` has a cron example for closing each lab.

### As a service

```bash
grader serve -c grader.toml        # or with systemd: deploy/grader.service
```

With `watch` in the configuration, every `watch_every` seconds the service checks which
repositories of those assignments have received a push and grades them. It is a single API query
per round, and GitHub does not need to be able to reach the server.

The API expects the `GRADER_API_TOKEN` token in the `Authorization: Bearer …` header:

| Request | What it does |
|---|---|
| `GET /health` | Status: queued jobs and pending scores. No token needed. |
| `POST /grade` | Body `{"classroom", "assignment", "students"?, "force"?, "until"?}`. Without `students`, grades everyone. Returns the job ids. |
| `GET /jobs` | The last 100 jobs. |
| `GET /jobs/<id>` | Status and summary of one job. |
| `POST /watch` | One watch round right now. |

```bash
curl -H "Authorization: Bearer $GRADER_API_TOKEN" -d '{"classroom":"grupo-test","assignment":"lab-2","students":["ana"]}' http://127.0.0.1:8080/grade
```

### Tools for preparing assignments

```bash
grader local ../prog-lab-2 ~/ana-submission                     # grades a folder, no GitHub
grader validate-assignment ../prog-lab-2 --solution ~/solutions/lab-2
grader sync-engine ../prog-lab-2 ../prog-lab-3                  # after changing engine.py
```

`validate-assignment` checks that:

- the tests load;
- the template has no workflows;
- the assignment's engine (`.grader/engine.py`) is the current one;
- the untouched template scores 0;
- the reference solution gets full marks.

Always run it before publishing an assignment.

## The tests

Every assignment has a `.grader/` folder with:

- `assignment.toml`: an `[assignment]` table with `tests` (the tests file, `tests.json` by
  default) and `protected` (patterns of files always taken from the template);
- `tests.json`: the tests;
- `engine.py`: the engine, which `check.py` uses on the student's computer.

`tests.json` uses the Classroom 50 test format: the same array as
`gh teacher assignment add --tests`. The test files already generated for the labs, projects and
exams work as they are; they only need to be called `tests.json`.

`engine.py` reproduces the semantics of the Classroom 50 grader (`runner.py`):

- **`io`:** `included` is a literal substring, `exact` compares ignoring leading and trailing
  whitespace, and `regex` uses multiline `re.search`.
- **`run`:** passes on the exit code.
- **`python`:** pytest splits the points among the cases that pass, using pytest's built-in
  JUnit report, no plugins.

One improvement over Classroom 50: when a test fails, the report points out the first line that
differs and the column, with spaces shown as `·`.

`extra_tests` in the configuration adds tests the student does not see. They are kept on the
server, not in the template.

## Security

- **Isolation.** Each submission runs in a new container:
  - without network;
  - without capabilities or privilege escalation;
  - with a read-only file system except for the submission folder and a 64 MB `/tmp`;
  - with memory, CPU and process limits;
  - with a `timeout` on each command.

  The container is destroyed at the end.
- **Safe download.** Tarballs are extracted with the `tarfile` `data` filter and without links: a
  submission cannot write outside its folder.
- **Untouchable tests.** The `.grader/` folder and the assignment's `protected` files are always
  taken from the template. Whatever the student changes there does not count.
- **The token never enters the container.**

## Known limitations

- **Individual assignments only.** Group assignments raise an error.
- **The submission date is the commit date.** Students can fake it. For a strict deadline, grade
  with `--until` right when it closes.
- **The Release can be deleted.** The grader creates it, but the student can delete it in their
  repository. The score that counts is the one in `scores.json`.
- **Watch failures.** If an automatic grading run fails (for example, GitHub does not respond), it
  is not retried until the next push. Check `GET /jobs` or the log, and run it again with
  `POST /grade`.

## Development

```bash
pip install -e ".[dev]"
pytest                                                     # 18 tests, no network or Docker
CLASSROOM50_SCHEMAS=~/classroom50/schemas pytest           # also validates against the schemas
```

The tests use a fake GitHub (`tests/fake_github.py`) and a copy of the lab 2 template in
`tests/fixtures/lab_02/template/`. They reproduce the pilot of 13 October:

| Submission | Score |
|---|---|
| Untouched template | 0/9 |
| Exercise 1 solved | 3/9 |
| Exercise 1 with an extra space | 0/9 |
| Everything solved | 9/9 |
