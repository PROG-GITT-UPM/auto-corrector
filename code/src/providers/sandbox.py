"""Where the student's code runs.

* `DockerSandbox` (the production one): one container per submission, created from the sandbox
  image through the Docker API (the `docker` CLI pointed at `DOCKER_HOST`, a socket proxy in the
  Compose deployment). The container has no network, no capabilities, no privilege escalation, a
  read-only root file system, memory, CPU and process limits, and runs as an unprivileged user.
  The submission is COPIED into it (`docker cp`) instead of bind-mounted, so the grader can itself
  run in a container and its work folder can be an ordinary volume. Each command runs with
  `docker exec` and `timeout` inside the container. The container is removed at the end, whatever
  happens.
* `LocalSandbox`: runs on the machine itself without isolating anything. Only for development and
  for the grader's tests; it refuses to run unless `insecure` is set.
"""
from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from ..modules import engine

log = logging.getLogger(__name__)

SANDBOX_DIR = "/submission"   # where the submission lives inside the container
SANDBOX_USER = "10001:10001"  # the unprivileged user of the sandbox image


class SandboxError(RuntimeError):
    pass


class LocalSandbox:
    def __init__(self, directory: Path, python: Optional[str] = None) -> None:
        self.directory = Path(directory)
        self._runner = engine.local_runner(self.directory, python)

    def __enter__(self) -> "LocalSandbox":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def run(self, command: str, stdin: Optional[str], timeout: int) -> engine.Execution:
        return self._runner(command, stdin, timeout)


class DockerSandbox:
    """One ephemeral container per submission."""

    def __init__(self, directory: Path, image: str, memory: str = "256m", cpus: str = "1",
                 processes: int = 128, docker: str = "docker") -> None:
        self.directory = Path(directory).resolve()
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.processes = processes
        self.docker = docker
        self.name = f"grader-{uuid.uuid4().hex[:12]}"
        self._created = False

    def _docker(self, *args: str, timeout: int = 120, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run([self.docker, *args], capture_output=True, timeout=timeout, **kwargs)

    def __enter__(self) -> "DockerSandbox":
        create = [
            "create", "--name", self.name, "--label", "grader.sandbox=1",
            "--network", "none",
            "--memory", self.memory, "--memory-swap", self.memory,
            "--cpus", self.cpus, "--pids-limit", str(self.processes),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=64m",
            # Anonymous volume: writable by the student code, removed together with the container.
            "--volume", SANDBOX_DIR, "--workdir", SANDBOX_DIR,
            "--user", SANDBOX_USER,
            "--env", "HOME=/tmp", "--env", "PYTHONUTF8=1", "--env", "PYTHONIOENCODING=utf-8",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            self.image, "sleep", "infinity",
        ]
        p = self._docker(*create, text=True)
        if p.returncode != 0:
            raise SandboxError(f"could not create the container: {p.stderr.strip()}")
        self._created = True
        try:
            # Start BEFORE copying: on start Docker fills the empty volume from the image, which
            # makes /submission belong to the sandbox user. Copying first would leave a non-empty
            # volume that Docker does not fill, with its root owned by root.
            p = self._docker("start", self.name, text=True)
            if p.returncode != 0:
                raise SandboxError(f"could not start the container: {p.stderr.strip()}")
            # The trailing "/." copies the folder's contents, not the folder itself. --archive
            # keeps the owner: the grader runs as the same uid as the sandbox user, so the
            # student code can modify its own files.
            p = self._docker("cp", "--archive", f"{self.directory}/.",
                             f"{self.name}:{SANDBOX_DIR}", text=True)
            if p.returncode != 0:
                raise SandboxError(f"could not copy the submission: {p.stderr.strip()}")
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        if self._created:
            self._docker("rm", "--force", "--volumes", self.name, text=True)
            self._created = False

    def _copy_back(self, relative: str) -> None:
        """Copies a file the student code produced in the container back to the local folder."""
        local = self.directory / relative
        local.unlink(missing_ok=True)
        self._docker("cp", f"{self.name}:{SANDBOX_DIR}/{relative}", str(local), text=True)

    def run(self, command: str, stdin: Optional[str], timeout: int) -> engine.Execution:
        # `timeout` inside the container kills the student's process; the outer timeout is the
        # safety net in case docker hangs.
        cmd = ["exec", "--interactive", self.name,
               "timeout", "--kill-after=2", str(timeout), "sh", "-c", command]
        try:
            p = self._docker(*cmd, input=stdin or "", text=True, encoding="utf-8",
                             errors="replace", timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            return engine.Execution(None, "", "", timed_out=True)
        if engine.PYTEST_REPORT in command:
            # The engine reads pytest's report from the local folder.
            self._copy_back(engine.PYTEST_REPORT)
        if p.returncode in (124, 137):
            return engine.Execution(None, p.stdout, p.stderr, timed_out=True)
        return engine.Execution(p.returncode, p.stdout, p.stderr)


def create_sandbox(sandbox_config: dict, directory: Path):
    kind = sandbox_config.get("type", "docker")
    if kind == "docker":
        return DockerSandbox(directory, image=sandbox_config.get("image", "grader-sandbox:3.12"),
                             memory=str(sandbox_config.get("memory", "256m")),
                             cpus=str(sandbox_config.get("cpus", "1")),
                             processes=int(sandbox_config.get("processes", 128)),
                             docker=sandbox_config.get("docker", "docker"))
    if kind == "local":
        if not sandbox_config.get("insecure"):
            raise SandboxError("local sandbox without isolation: set SANDBOX_INSECURE=true if you "
                               "really want to run student code without a container")
        return LocalSandbox(directory, sandbox_config.get("python"))
    raise SandboxError(f"unknown sandbox type: {kind}")


def remove_leftovers(docker: str = "docker") -> int:
    """Removes sandbox containers left behind by a grader that died half-way. Returns how many."""
    try:
        p = subprocess.run([docker, "ps", "--all", "--quiet", "--filter", "label=grader.sandbox=1"],
                           capture_output=True, text=True, timeout=30)
        ids = p.stdout.split() if p.returncode == 0 else []
        if ids:
            subprocess.run([docker, "rm", "--force", "--volumes", *ids], capture_output=True,
                           timeout=120)
        return len(ids)
    except (OSError, subprocess.TimeoutExpired):
        log.warning("could not look for leftover sandbox containers")
        return 0


def image_available(image: str, docker: str = "docker") -> Optional[bool]:
    """Whether the sandbox image exists (None if Docker cannot be reached)."""
    try:
        p = subprocess.run([docker, "image", "inspect", image], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode == 0:
        return True
    return False if b"No such image" in p.stderr or b"not found" in p.stderr.lower() else None
