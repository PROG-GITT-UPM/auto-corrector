"""Configuración del corrector (`corrector.toml`). Ver `corrector.example.toml`."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class ErrorConfig(ValueError):
    pass


@dataclass
class Tarea:
    """Una tarea de Classroom 50 que corrige este corrector."""
    clase: str
    tarea: str
    plantilla: str                       # «org/repo» de la práctica: de ahí salen las pruebas
    ref: str = "main"
    carpeta_corrector: str = ".corrector"
    publicar_release: bool = True        # Release con el resultado en el repositorio del alumno
    guardar_nota: bool = True            # nota en scores.json de Classroom 50
    pruebas_extra: Optional[Path] = None  # pruebas que el alumno no ve (fichero local)

    @property
    def clave(self) -> str:
        return f"{self.clase}/{self.tarea}"


@dataclass
class Config:
    organizacion: str
    token: str
    repo_config: str = "classroom50"
    api: str = "https://api.github.com"
    web: str = "https://github.com"
    sandbox: dict = field(default_factory=lambda: {"tipo": "docker"})
    trabajo: Path = Path("/var/lib/corrector")
    servicio: dict = field(default_factory=dict)
    tareas: dict = field(default_factory=dict)

    def tarea(self, clase: str, tarea: str) -> Tarea:
        try:
            return self.tareas[f"{clase}/{tarea}"]
        except KeyError:
            raise ErrorConfig(f"la tarea {clase}/{tarea} no está en la configuración "
                              f"(hay: {', '.join(self.tareas) or 'ninguna'})") from None


def cargar(ruta: Path, exigir_token: bool = True) -> Config:
    ruta = Path(ruta)
    try:
        datos = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ErrorConfig(f"no existe {ruta}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ErrorConfig(f"{ruta}: {exc}") from None

    gh = datos.get("github", {})
    if "organizacion" not in gh:
        raise ErrorConfig("falta [github] organizacion")
    variable = gh.get("token_env", "GITHUB_TOKEN")
    token = os.environ.get(variable, "")
    if exigir_token and not token:
        raise ErrorConfig(f"falta el token de GitHub en la variable de entorno {variable}")

    tareas: dict = {}
    for clave, t in datos.get("tareas", {}).items():
        if clave.count("/") != 1:
            raise ErrorConfig(f'[tareas."{clave}"]: la clave debe ser "clase/tarea"')
        clase, nombre = clave.split("/")
        if "plantilla" not in t:
            raise ErrorConfig(f'[tareas."{clave}"]: falta plantilla ("org/repo")')
        extra = t.get("pruebas_extra")
        tareas[clave] = Tarea(
            clase=clase, tarea=nombre, plantilla=t["plantilla"], ref=t.get("ref", "main"),
            carpeta_corrector=t.get("carpeta_corrector", ".corrector"),
            publicar_release=t.get("publicar_release", True),
            guardar_nota=t.get("guardar_nota", True),
            pruebas_extra=(ruta.parent / extra).resolve() if extra else None,
        )

    return Config(
        organizacion=gh["organizacion"], token=token,
        repo_config=gh.get("repo_config", "classroom50"),
        api=gh.get("api", "https://api.github.com"), web=gh.get("web", "https://github.com"),
        sandbox=datos.get("sandbox", {"tipo": "docker"}),
        trabajo=Path(datos.get("trabajo", {}).get("carpeta", "/var/lib/corrector")),
        servicio=datos.get("servicio", {}),
        tareas=tareas,
    )
