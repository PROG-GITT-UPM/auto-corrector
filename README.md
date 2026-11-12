# auto-grader

A service that grades the course's Python assignments on our own servers and records the scores in
Classroom 50, without GitHub Actions. It starts with `docker compose up -d` and waits for
requests; its API is documented, and can be tried, at `/docs`.

GitHub only stores the repositories. Classroom 50 is still the interface: enrollment, accepting
the assignment, the submissions page and the score export. Student code runs here, each submission
in its own throw-away container.

```
student ── push ──▶ student repo (no workflows: spends no minutes)
                          │
       auto-grader ◀──────┘  (on request, or on every push if watching is on)
           │
           ├─ runs the tests in a sandbox container without network
           ├─▶ Release "Grading: 7/9" in the student repo (what the student sees)
           └─▶ score in classroom50/<classroom>/scores.json (what Classroom 50 sees)
```

> **Keep this repository private.** `code/tests/fixtures/lab_02/solution/` holds the reference
> solutions of lab 2.

Conventions for working on this repository (language, layout, the shared engine, tests) are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Running it

Requires a Linux machine with Docker and the Compose plugin.

```bash
git clone https://github.com/PROG-GITT-UPM/auto-grader.git && cd auto-grader
scripts/setup.sh              # creates .env and the data folders
nano .env                     # fill in the GitHub token, the API token and the passwords
docker compose up -d --build
```

Open `http://localhost:8000/docs`, press **Authorize**, paste `GRADER_API_TOKEN`, and every
request can be tried from there. `GET /health` (no token) says whether the database and the
sandbox image are ready.

What starts:

| Service | What it is |
|---|---|
| `grader` | The API and the background workers (port 8000, on 127.0.0.1 by default: see `GRADER_BIND`). |
| `postgres` | PostgreSQL 16 with all the state, in the host folder `POSTGRES_DATA_DIR` (default `./data/postgres`). |
| `docker-proxy` | Lets the grader create sandbox containers without giving it the Docker socket. |
| `sandbox` | Not a running service: it builds the `grader-sandbox:3.12` image that grading containers are created from. |
| `pgadmin` | Optional database console: `docker compose --profile admin up -d`, at `http://localhost:8080`. |

To reach the API from another computer, either keep `GRADER_BIND=127.0.0.1` and use an SSH tunnel
(`ssh -L 8000:localhost:8000 server`), or set `GRADER_BIND=0.0.0.0` and put HTTPS in front (a
reverse proxy such as nginx or Caddy). The API token travels in every request, so do not expose it
over plain HTTP.

### The GitHub token

A *fine-grained* token with:

- **Resource owner:** the organization.
- **Repositories:** all of them (student repositories are created on the fly).
- **Permissions:**
  - **Contents: Read and write**, to download code, create Releases and write `scores.json`.
  - **Metadata: Read**.

The token expires. Write the date down and renew it before the semester starts.

## Using it

### 1. Create the assignment in Classroom 50

With two options:

- **Grading: Manual**, with the assignment's points.
- **Do not use the built-in autograder** (`no_autograder`). The student repository is then an exact
  copy of the template, without workflows, and pushes trigger nothing in Actions.

An assignment created without the second option keeps the Classroom 50 autograder, so every push
spends minutes even if this service also grades it.

### 2. Add it to the service

The simplest way is to sync with Classroom 50:

```
POST /assignments/sync      {}                                   all classrooms
POST /assignments/sync      {"classroom": "grupo-test"}          one classroom
POST /assignments/sync      {"dry_run": true}                    only report what would change
```

It reads the assignments declared in Classroom 50 and:

- **adds** the gradable ones the service does not have: individual, with a template, and created
  with "Do not use the built-in autograder". Watching starts off;
- **updates** those whose template or branch changed, keeping their hidden tests and watch
  settings;
- **skips** the ones it cannot grade, saying why (for example, they still use the Classroom 50
  autograder);
- **reports as `missing`** the ones the service has but Classroom 50 no longer has. They are only
  removed with `"remove_missing": true`, because removing an assignment also removes its hidden
  tests.

An assignment can also be added or changed by hand:

```
PUT /assignments/{classroom}/{assignment}       {"template": "PROG-GITT-UPM/prog-lab-2"}
```

`classroom` and `assignment` are the Classroom 50 slugs. Before saving, the service checks that the
assignment exists in Classroom 50 and is individual, and that the template's tests load. The
response lists warnings: for example, if the assignment still uses the Classroom 50 autograder, or
if the points do not add up to the ones Classroom 50 expects.

### 3. Grade

On request:

```
POST /grade      {"classroom": "grupo-test", "assignment": "lab-2"}
POST /grade      {"classroom": "grupo-test", "assignment": "lab-2", "students": ["ana"]}
POST /grade      {"classroom": "grupo-test", "assignment": "lab-2",
                  "until": "2026-11-15T22:59:00Z"}
```

- Without `students`, it grades everyone who accepted the assignment.
- **`until`** grades the latest commit before that date: what was submitted on time.
- **Repeated requests:** a commit already graded with the same tests is not graded again (the job
  ends as `already graded`), unless `force` is set. If the tests change, it is graded again.

Or on every push, turning watching on (it is off by default for every assignment):

```
PUT /assignments/{classroom}/{assignment}/watch     {"enabled": true, "interval_seconds": 120}
```

Every `interval_seconds` the service checks which students have pushed and queues them. It is a
single GitHub query per round, and GitHub does not need to reach the server. `{"enabled": false}`
turns it off; `POST /assignments/{classroom}/{assignment}/watch/run` does one round right away.

### 4. Follow the jobs and the scores

- `GET /jobs` (filters: `classroom`, `assignment`, `user`, `status`) and `GET /jobs/{id}`: status,
  score, the result of each test, the Release link and errors.
- Scores reach Classroom 50 in batches, every `GRADER_SAVE_EVERY` seconds, in one commit per
  classroom. `GET /scores/pending` lists the ones waiting, and `POST /scores/save` writes them now.

### Hidden tests

Students run the tests in the template's `.grader/tests.json` with `check.py`. The service runs
those and, optionally, hidden tests that only it knows:

```
PUT /assignments/{classroom}/{assignment}/hidden-tests      {"tests": [ ... ]}
```

They use the same Classroom 50 format as `tests.json`, with `input` and `expected` inline. In their
Release, students see whether each hidden test passed, but not its input, the expected output or
their own output. Changing them makes already graded submissions be graded again.

## How scores reach Classroom 50

The score the service writes is the same one the web writes when a teacher enters a score by hand:
an entry with `"override": true` in `scores.json`, modelled on the web's code
(`web/src/domain/assignments/scoreOverride.ts`). That is why it shows up on the submissions page
and in the "Download scores" CSV, and why the Classroom 50 score collector leaves it alone.

- **`[skip ci]` on every score commit.** Every commit to the `classroom50` repository triggers its
  `publish-pages` workflow, which does spend minutes. Entering scores by hand on the web does
  trigger it, once per score.
- **Manual scores from teachers are kept.** If a teacher enters a score by hand on the web, grading
  again does not overwrite it (it is reported as `kept`); `force` does.

The service's scores are recognised by the `submit/manual-grader-` prefix. Checked against the
Classroom 50 code of 5 November 2026 (commit `b6d81a4`). If Classroom 50 changes the format of
`scores.json`, review `code/src/modules/publish.py`.

## Stopping, moving and backing up

All the state is in PostgreSQL: the assignments, their hidden tests and watch settings, the queue,
the grading history and the scores not yet written to Classroom 50. Its data lives in the host
folder `POSTGRES_DATA_DIR`; the other folders (`PGADMIN_DATA_DIR`, `BACKUP_DIR`) are also set in
`.env`. The grader's own work folder is a cache inside its container and is not kept.

- **Stopping and starting** resumes where it was: jobs left half-way are queued again, pending
  scores are written on the next round, and watching continues from the last push it saw.

  ```bash
  scripts/stop.sh              # stop (scripts/stop.sh --backup makes a backup first)
  docker compose up -d         # start again
  ```

  `stop.sh` stops the grader first, so it can write the pending scores while the database is still
  up, and removes any grading container left half-way. A reboot also works: the containers start
  again on their own.
- **Backups:**

  ```bash
  scripts/backup.sh                # dump into BACKUP_DIR, keeping the newest BACKUP_KEEP
  scripts/backup.sh --with-env     # also a copy of .env (the secrets)
  ```

  It can run while the service is working. To run it every night, add to the crontab of a user
  that can use Docker: `0 3 * * * /path/to/auto-grader/scripts/backup.sh`. Dumps contain the hidden
  tests: keep them as private as the repository.
- **Restoring** replaces the current state with a dump. It stops the grader, restores and starts it
  again:

  ```bash
  scripts/restore.sh data/backups/grader-20261110-030000Z.dump
  ```

- **Moving to another machine:** clone the repository, copy `.env`, run `scripts/setup.sh`, start
  with `docker compose up -d --build` and restore the latest dump with `scripts/restore.sh`.
  Copying the `POSTGRES_DATA_DIR` folder (with the service stopped) also works, but only to the
  same PostgreSQL major version; a dump works across versions. `.env` holds the secrets and is in
  no dump unless you ask for it with `--with-env`.

## Removing the service

```bash
scripts/destroy.sh             # final backup first; keeps BACKUP_DIR and .env
scripts/destroy.sh --no-backup # no final backup; keeps BACKUP_DIR and .env
scripts/destroy.sh --purge     # removes everything, backups and .env included
```

It removes the containers, the networks, the grader and sandbox images and the data folders
(`POSTGRES_DATA_DIR`, `PGADMIN_DATA_DIR`), after asking you to type `delete`. It needs `sudo` for
the data folders, which belong to the containers' users. Nothing in GitHub is touched: the scores
already in Classroom 50 and the Releases in the student repositories stay. To install it again:
`scripts/setup.sh && docker compose up -d --build`, then `scripts/restore.sh` with the last backup.

## Security

- **Sandbox containers:**
  - no network;
  - no capabilities or privilege escalation, and an unprivileged user;
  - read-only file system except the submission folder and a 64 MB `/tmp`;
  - memory, CPU and process limits;
  - a `timeout` on each command;
  - removed at the end. Containers left by a crash are removed when the service starts.
- **No Docker socket for the grader.** It talks to Docker through `docker-proxy`, which only lets
  through container and image operations, on an internal network with no way out.
- **Safe download.** Tarballs are extracted with the `tarfile` `data` filter and without links.
- **Untouchable tests.** The `.grader/` folder and the assignment's `protected` files are always
  taken from the template. Whatever the student changes there does not count.
- **Secrets** (GitHub token, API token, database password) are only in `.env`, never in the
  database or the image, and never reach the sandbox containers.

## Known limitations

- **Individual assignments only.** Group assignments are rejected.
- **The submission date is the commit date.** Students can fake it. For a strict deadline, grade
  with `until` right when it closes.
- **The Release can be deleted.** The student can delete it in their repository. The score that
  counts is the one in `scores.json`.
- **Watch failures.** If grading a push fails (for example, GitHub does not respond), it is not
  retried until the next push. Check `GET /jobs?status=error` and ask again with `POST /grade`.

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md#tests). In short:

```bash
docker run -d --name grader-test-db -p 5433:5432 -e POSTGRES_PASSWORD=test postgres:16
pip install -r requirements-dev.txt
cd code && TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres pytest
```

The tests reproduce the pilot of 13 October on the lab 2 template:

| Submission | Score |
|---|---|
| Untouched template | 0/9 |
| Exercise 1 solved | 3/9 |
| Exercise 1 with an extra space | 0/9 |
| Everything solved | 9/9 |
