"""Where the student's code runs.

* `DockerSandbox` (the production one): one container per submission, without network, without
  privileges, with a read-only file system except for the submission folder and /tmp, and with
  memory, CPU and process limits. Each command is launched with `docker exec` and `timeout`
  inside the container. The container is destroyed at the end, whatever happens.
* `LocalSandbox`: runs on the server itself without isolating anything. Only for development and
  for the grader's tests; the grader refuses to use it without `insecure = true` in the config.
"""
from __future__ import annotations

import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from . import engine

log = logging.getLogger(__name__)


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
        self._active = False

    def __enter__(self) -> "DockerSandbox":
        uid, gid = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else (65534, 65534)
        command = [
            self.docker, "run", "--detach", "--rm", "--name", self.name,
            "--network", "none",
            "--memory", self.memory, "--memory-swap", self.memory,
            "--cpus", self.cpus, "--pids-limit", str(self.processes),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=64m",
            "--user", f"{uid}:{gid}",
            "--env", "HOME=/tmp", "--env", "PYTHONUTF8=1", "--env", "PYTHONIOENCODING=utf-8",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--volume", f"{self.directory}:/submission:rw", "--workdir", "/submission",
            self.image, "sleep", "infinity",
        ]
        p = subprocess.run(command, capture_output=True, text=True)
        if p.returncode != 0:
            raise SandboxError(f"could not start the container: {p.stderr.strip()}")
        self._active = True
        return self

    def __exit__(self, *exc: object) -> None:
        if self._active:
            subprocess.run([self.docker, "rm", "--force", self.name], capture_output=True)
            self._active = False

    def run(self, command: str, stdin: Optional[str], timeout: int) -> engine.Execution:
        # `timeout` inside the container kills the student's process; the outer timeout is the
        # safety net in case docker hangs.
        cmd = [self.docker, "exec", "--interactive", self.name,
               "timeout", "--kill-after=2", str(timeout), "sh", "-c", command]
        try:
            p = subprocess.run(cmd, input=stdin or "", capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            return engine.Execution(None, "", "", timed_out=True)
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
            raise SandboxError("local sandbox without isolation: set `insecure = true` in "
                               "[sandbox] if you really want to run student code without a "
                               "container")
        return LocalSandbox(directory, sandbox_config.get("python"))
    raise SandboxError(f"unknown sandbox type: {kind}")
