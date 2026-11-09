"""Dónde se ejecuta el código del alumno.

* `SandboxDocker` (el de producción): un contenedor por entrega, sin red, sin privilegios, con el
  sistema de ficheros de solo lectura salvo la carpeta de la entrega y /tmp, y con límites de
  memoria, CPU y procesos. Cada orden se lanza con `docker exec` y `timeout` dentro del contenedor.
  El contenedor se destruye al terminar, pase lo que pase.
* `SandboxLocal`: ejecuta en el propio servidor sin aislar nada. Solo para desarrollo y para las
  pruebas del corrector; el corrector se niega a usarlo sin `inseguro = true` en la configuración.
"""
from __future__ import annotations

import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from . import motor

log = logging.getLogger(__name__)


class ErrorSandbox(RuntimeError):
    pass


class SandboxLocal:
    def __init__(self, directorio: Path, python: Optional[str] = None) -> None:
        self.directorio = Path(directorio)
        self._ejecutor = motor.ejecutor_local(self.directorio, python)

    def __enter__(self) -> "SandboxLocal":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def ejecutar(self, orden: str, entrada: Optional[str], timeout: int) -> motor.Ejecucion:
        return self._ejecutor(orden, entrada, timeout)


class SandboxDocker:
    """Un contenedor efímero por entrega."""

    def __init__(self, directorio: Path, imagen: str, memoria: str = "256m", cpus: str = "1",
                 procesos: int = 128, docker: str = "docker") -> None:
        self.directorio = Path(directorio).resolve()
        self.imagen = imagen
        self.memoria = memoria
        self.cpus = cpus
        self.procesos = procesos
        self.docker = docker
        self.nombre = f"corrector-{uuid.uuid4().hex[:12]}"
        self._activo = False

    def __enter__(self) -> "SandboxDocker":
        uid, gid = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else (65534, 65534)
        orden = [
            self.docker, "run", "--detach", "--rm", "--name", self.nombre,
            "--network", "none",
            "--memory", self.memoria, "--memory-swap", self.memoria,
            "--cpus", self.cpus, "--pids-limit", str(self.procesos),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=64m",
            "--user", f"{uid}:{gid}",
            "--env", "HOME=/tmp", "--env", "PYTHONUTF8=1", "--env", "PYTHONIOENCODING=utf-8",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--volume", f"{self.directorio}:/entrega:rw", "--workdir", "/entrega",
            self.imagen, "sleep", "infinity",
        ]
        p = subprocess.run(orden, capture_output=True, text=True)
        if p.returncode != 0:
            raise ErrorSandbox(f"no se pudo arrancar el contenedor: {p.stderr.strip()}")
        self._activo = True
        return self

    def __exit__(self, *exc: object) -> None:
        if self._activo:
            subprocess.run([self.docker, "rm", "--force", self.nombre], capture_output=True)
            self._activo = False

    def ejecutar(self, orden: str, entrada: Optional[str], timeout: int) -> motor.Ejecucion:
        # `timeout` dentro del contenedor mata el proceso del alumno; el timeout de fuera es la red
        # de seguridad por si docker se queda colgado.
        cmd = [self.docker, "exec", "--interactive", self.nombre,
               "timeout", "--kill-after=2", str(timeout), "sh", "-c", orden]
        try:
            p = subprocess.run(cmd, input=entrada or "", capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            return motor.Ejecucion(None, "", "", agotado=True)
        if p.returncode in (124, 137):
            return motor.Ejecucion(None, p.stdout, p.stderr, agotado=True)
        return motor.Ejecucion(p.returncode, p.stdout, p.stderr)


def crear_sandbox(config_sandbox: dict, directorio: Path):
    tipo = config_sandbox.get("tipo", "docker")
    if tipo == "docker":
        return SandboxDocker(directorio, imagen=config_sandbox.get("imagen", "corrector-sandbox:3.12"),
                             memoria=str(config_sandbox.get("memoria", "256m")),
                             cpus=str(config_sandbox.get("cpus", "1")),
                             procesos=int(config_sandbox.get("procesos", 128)),
                             docker=config_sandbox.get("docker", "docker"))
    if tipo == "local":
        if not config_sandbox.get("inseguro"):
            raise ErrorSandbox("sandbox local sin aislamiento: pon `inseguro = true` en [sandbox] "
                               "si de verdad quieres ejecutar código de alumnos sin contenedor")
        return SandboxLocal(directorio, config_sandbox.get("python"))
    raise ErrorSandbox(f"tipo de sandbox desconocido: {tipo}")
