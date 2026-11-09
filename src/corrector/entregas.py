"""Qué se corrige: localizar los repositorios de una tarea, decidir el commit y preparar la carpeta.

Classroom 50 crea un repositorio por alumno llamado `<clase>-<tarea>-<usuario>`. Con la opción
«Do not use the built-in autograder» (`no_autograder`) el repositorio es una copia exacta de la
plantilla, sin workflows: los push no lanzan nada en GitHub Actions.

La carpeta que se corrige es el código del alumno con la carpeta `.corrector/` (pruebas y motor)
y los ficheros protegidos copiados SIEMPRE de la plantilla de la práctica. Así da igual que el
alumno haya tocado sus pruebas o sus ficheros de datos.
"""
from __future__ import annotations

import fnmatch
import io
import json
import logging
import shutil
import tarfile
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Config, Tarea
from .github import ErrorGitHub, GitHub

log = logging.getLogger(__name__)


@dataclass
class Entrega:
    organizacion: str
    clase: str
    tarea: str
    usuario: str
    repo: str
    sha: str
    fecha: str               # fecha del commit (committer date), UTC, ISO 8601 con Z
    rama: str
    web: str = "https://github.com"

    @property
    def sha7(self) -> str:
        return self.sha[:7]

    @property
    def url_repo(self) -> str:
        return f"{self.web}/{self.organizacion}/{self.repo}"

    @property
    def url_commit(self) -> str:
        return f"{self.url_repo}/commit/{self.sha}"

    @property
    def url_revision(self) -> str:
        # La rama `feedback` está congelada en el commit inicial (la abre Classroom 50 con la
        # pull request «Feedback»): la comparación enseña todo lo que ha hecho el alumno.
        return f"{self.url_repo}/compare/feedback...{self.sha}"


@dataclass
class Plantilla:
    repo: str
    sha: str
    carpeta: Path            # plantilla descomprimida
    practica: dict           # contenido de .corrector/practica.toml

    @property
    def sha7(self) -> str:
        return self.sha[:7]


def fecha_iso(texto: str) -> str:
    """Normaliza una fecha de GitHub a «AAAA-MM-DDTHH:MM:SSZ» en UTC."""
    d = datetime.fromisoformat(texto.replace("Z", "+00:00")).astimezone(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def extraer_tarball(datos: bytes, destino: Path) -> None:
    """Descomprime el tarball de GitHub quitando la carpeta raíz («org-repo-sha/»).

    Usa el filtro `data` de tarfile: rechaza rutas absolutas, `..`, enlaces que salen de la carpeta,
    dispositivos y permisos especiales. Un alumno no puede escribir fuera de `destino`."""
    destino.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(datos), mode="r:gz") as tar:
        miembros = []
        for m in tar.getmembers():
            partes = m.name.split("/", 1)
            if len(partes) < 2 or not partes[1]:
                continue
            m.name = partes[1]
            if m.islnk() or m.issym():
                continue  # los enlaces no hacen falta para corregir y son una vía de escape
            miembros.append(m)
        tar.extractall(destino, members=miembros, filter="data")


class Repositorios:
    """Acceso a GitHub para una configuración: tareas de Classroom 50, alumnos y plantillas."""

    def __init__(self, gh: GitHub, cfg: Config, ttl_plantilla: float = 300.0) -> None:
        self.gh = gh
        self.cfg = cfg
        self.ttl_plantilla = ttl_plantilla
        self._plantillas: dict = {}
        self._cerrojo = threading.Lock()

    # ─── Configuración de Classroom 50 ───────────────────────────────────────

    def tareas_classroom(self, clase: str) -> list:
        texto, _ = self.gh.leer_fichero(self.cfg.organizacion, self.cfg.repo_config,
                                        f"{clase}/assignments.json")
        datos = json.loads(texto)
        return datos.get("assignments", []) if isinstance(datos, dict) else datos

    def tarea_classroom(self, clase: str, tarea: str) -> dict:
        for t in self.tareas_classroom(clase):
            if t.get("slug") == tarea:
                return t
        raise ErrorGitHub(404, f"la tarea {tarea} no está en {clase}/assignments.json")

    # ─── Alumnos ─────────────────────────────────────────────────────────────

    def listar_repos(self, tarea: Tarea) -> list:
        """[(usuario, nombre del repo, pushed_at)] de la tarea, el último push primero.

        Una sola consulta paginada a la organización (100 repos por página) sirve para todos los
        alumnos: no hace falta una consulta por alumno para saber quién ha hecho push."""
        prefijo = f"{tarea.clase}-{tarea.tarea}-"
        # Otras tareas cuyo nombre empieza igual («lab-02» y «lab-02-b»): sus repos no son de esta.
        otros = [f"{tarea.clase}-{t['slug']}-" for t in self.tareas_classroom(tarea.clase)
                 if t.get("slug", "") != tarea.tarea and f"{tarea.clase}-{t.get('slug', '')}-"
                 .startswith(prefijo)]
        resultado = []
        for repo in self.gh.paginar(f"/orgs/{self.cfg.organizacion}/repos", type="all",
                                    sort="pushed", direction="desc"):
            nombre = repo["name"]
            if not nombre.startswith(prefijo) or any(nombre.startswith(o) for o in otros):
                continue
            usuario = nombre[len(prefijo):]
            if usuario:
                resultado.append((usuario, nombre, repo.get("pushed_at") or ""))
        return resultado

    def resolver(self, tarea: Tarea, usuario: str, hasta: Optional[str] = None) -> Entrega:
        """La entrega de un alumno: el último commit de la rama principal (o el último antes de
        `hasta`, para corregir lo entregado en plazo)."""
        org = self.cfg.organizacion
        repo = f"{tarea.clase}-{tarea.tarea}-{usuario}"
        info = self.gh.get(f"/repos/{org}/{repo}")
        rama = info["default_branch"]
        params = {"sha": rama, "per_page": 1}
        if hasta:
            params["until"] = hasta
        commits = self.gh.get(f"/repos/{org}/{repo}/commits", **params)
        if not commits:
            raise ErrorGitHub(404, f"{repo} no tiene commits" + (f" antes de {hasta}" if hasta else ""))
        c = commits[0]
        return Entrega(organizacion=org, clase=tarea.clase, tarea=tarea.tarea, usuario=usuario,
                       repo=repo, sha=c["sha"], fecha=fecha_iso(c["commit"]["committer"]["date"]),
                       rama=rama, web=self.cfg.web)

    # ─── Plantilla de la práctica ────────────────────────────────────────────

    def plantilla(self, tarea: Tarea) -> Plantilla:
        """La plantilla descomprimida en su versión actual (se refresca cada `ttl_plantilla` s)."""
        clave = (tarea.plantilla, tarea.ref)
        with self._cerrojo:
            guardada = self._plantillas.get(clave)
            if guardada and time.monotonic() - guardada[0] < self.ttl_plantilla:
                return guardada[1]
            propietario, repo = tarea.plantilla.split("/", 1)
            sha = self.gh.get(f"/repos/{propietario}/{repo}/commits/{tarea.ref}")["sha"]
            if guardada and guardada[1].sha == sha:
                self._plantillas[clave] = (time.monotonic(), guardada[1])
                return guardada[1]
            carpeta = self.cfg.trabajo / "plantillas" / f"{propietario}-{repo}-{sha[:12]}"
            if not carpeta.is_dir():
                temporal = carpeta.with_name(carpeta.name + ".tmp")
                shutil.rmtree(temporal, ignore_errors=True)
                extraer_tarball(self.gh.descargar_tarball(propietario, repo, sha), temporal)
                temporal.rename(carpeta)
            p = Plantilla(repo=tarea.plantilla, sha=sha, carpeta=carpeta,
                          practica=leer_practica(carpeta / tarea.carpeta_corrector))
            self._plantillas[clave] = (time.monotonic(), p)
            return p

    def descargar(self, entrega: Entrega, destino: Path) -> None:
        extraer_tarball(self.gh.descargar_tarball(entrega.organizacion, entrega.repo, entrega.sha),
                        destino)


def leer_practica(carpeta_corrector: Path) -> dict:
    ruta = carpeta_corrector / "practica.toml"
    if not ruta.is_file():
        raise FileNotFoundError(f"la plantilla no tiene {ruta.name} en {carpeta_corrector.name}/")
    return tomllib.loads(ruta.read_text(encoding="utf-8")).get("practica", {})


def preparar_carpeta(codigo_alumno: Path, plantilla: Plantilla, carpeta_corrector: str) -> list:
    """Sustituye en la entrega la carpeta del corrector y los ficheros protegidos por los de la
    plantilla. Devuelve la lista de ficheros protegidos restaurados (para el informe)."""
    destino = codigo_alumno / carpeta_corrector
    shutil.rmtree(destino, ignore_errors=True)
    if destino.exists():
        destino.unlink()
    shutil.copytree(plantilla.carpeta / carpeta_corrector, destino)
    restaurados = []
    for patron in plantilla.practica.get("protegidos", []):
        for origen in sorted(plantilla.carpeta.rglob("*")):
            relativa = origen.relative_to(plantilla.carpeta).as_posix()
            if origen.is_file() and fnmatch.fnmatch(relativa, patron):
                copia = codigo_alumno / relativa
                copia.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origen, copia)
                restaurados.append(relativa)
    return restaurados
