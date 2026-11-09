"""Publicar una corrección sin usar GitHub Actions.

1. **Release en el repositorio del alumno** (`correccion/<fecha>-<commit>`): el alumno ve el
   resultado de cada prueba. Lleva `result.json` como adjunto.
2. **Nota en Classroom 50**: una entrada `"override": true` en `<clase>/scores.json` del
   repositorio `classroom50`. Es exactamente lo que escribe la web de Classroom 50 cuando un
   profesor pone una nota a mano (`web/src/domain/assignments/scoreOverride.ts`), así que la nota
   aparece en la página de entregas y en el CSV de «Download scores», y el recolector de notas no
   la toca (`collect_scores.py` conserva las entradas con override).

Cada commit en el repositorio `classroom50` dispara su workflow `publish-pages`, que sí gasta
minutos de Actions. Por eso las notas se acumulan y se escriben en UN commit por clase, con
`[skip ci]` en el mensaje (GitHub no lanza workflows de push para esos commits).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from .entregas import Entrega
from .github import Conflicto, ErrorGitHub, GitHub

log = logging.getLogger(__name__)

PREFIJO_TAG = "correccion/"
PREFIJO_ENVIO = "submit/manual-corrector-"
PREFIJO_MANUAL = "submit/manual-"
MAX_HISTORIAL = 20


def huella_carpeta(carpeta: Path) -> str:
    """Huella de las pruebas: si cambian, las entregas ya corregidas se vuelven a corregir."""
    h = hashlib.sha256()
    for f in sorted(p for p in carpeta.rglob("*") if p.is_file()):
        h.update(f.relative_to(carpeta).as_posix().encode())
        h.update(b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def marca_huella(huella: str) -> str:
    return f"<!-- corrector:huella={huella} -->"


# ─── Release ─────────────────────────────────────────────────────────────────

def release_existente(gh: GitHub, entrega: Entrega, huella: str) -> Optional[dict]:
    """El Release de una corrección anterior del mismo commit con las mismas pruebas, si lo hay."""
    try:
        releases = gh.get(f"/repos/{entrega.organizacion}/{entrega.repo}/releases", per_page=50)
    except ErrorGitHub as exc:
        if exc.estado == 404:
            return None
        raise
    for r in releases:
        tag = r.get("tag_name", "")
        if (tag.startswith(PREFIJO_TAG) and tag.endswith(f"-{entrega.sha7}")
                and marca_huella(huella) in (r.get("body") or "")):
            return r
    return None


def publicar_release(gh: GitHub, entrega: Entrega, cuerpo: str, resultado: dict,
                     huella: str, sello: str) -> str:
    """Crea el Release (y su tag) en el commit corregido; devuelve su URL."""
    tag = f"{PREFIJO_TAG}{sello.replace(':', '-')}-{entrega.sha7}"
    puntos, maximo = resultado["score"], resultado["max-score"]
    release = gh.enviar("POST", f"/repos/{entrega.organizacion}/{entrega.repo}/releases", {
        "tag_name": tag,
        "target_commitish": entrega.sha,
        "name": f"Corrección: {puntos}/{maximo} · {entrega.sha7}",
        "body": f"{cuerpo}\n\n{marca_huella(huella)}",
        "make_latest": "true",
    })
    resultado["release"] = release["html_url"]
    gh.subir_asset(release["upload_url"], "result.json",
                   json.dumps(resultado, indent=2, ensure_ascii=False).encode("utf-8"))
    return release["html_url"]


# ─── Notas en scores.json ────────────────────────────────────────────────────

def _es_manual_del_profesor(registro: dict) -> bool:
    envio = str(registro.get("submission", ""))
    return envio.startswith(PREFIJO_MANUAL) and not envio.startswith(PREFIJO_ENVIO)


def aplicar_nota(scores: dict, tarea: str, tipo: str, registro: dict,
                 forzar: bool = False) -> str:
    """Mete un registro de corrección en `scores` (modifica el dict). Devuelve qué ha hecho:
    "nueva", "actualizada" o "respetada" (si el profesor puso una nota a mano en la web, no se
    pisa salvo con `forzar`)."""
    scores.setdefault("schema", "classroom50/scores/v1")
    cubos = scores.setdefault("assignments", {})
    cubo = cubos.setdefault(tarea, {"type": tipo, "entries": []})
    cubo["type"] = tipo
    entradas = cubo.setdefault("entries", [])
    propietario = registro["owner"].strip().lower()
    registro = {k: v for k, v in registro.items() if k != "assignment"}
    for i, entrada in enumerate(entradas):
        if str(entrada.get("owner", "")).strip().lower() != propietario:
            continue
        previos = entrada.get("submissions") or []
        if previos and _es_manual_del_profesor(previos[0]) and not forzar:
            return "respetada"
        # Historial: correcciones anteriores del corrector que no son posteriores a esta (al
        # corregir lo entregado en plazo con --hasta, lo de después no debe quedar delante), y las
        # entregas autocorregidas por Classroom 50 si las hubiera.
        anteriores = [s for s in previos if not _es_manual_del_profesor(s)
                      and not (str(s.get("submission", "")).startswith(PREFIJO_ENVIO)
                               and s.get("datetime", "") > registro["datetime"])]
        entradas[i] = {**entrada, "owner": entrada.get("owner") or registro["owner"],
                       "override": True,
                       "submissions": [registro] + anteriores[:MAX_HISTORIAL - 1]}
        return "actualizada"
    entradas.append({"owner": registro["owner"], "override": True, "submissions": [registro]})
    return "nueva"


class Calificaciones:
    """Acumula notas y las escribe en scores.json en un solo commit por clase."""

    def __init__(self, gh: GitHub, organizacion: str, repo_config: str) -> None:
        self.gh = gh
        self.organizacion = organizacion
        self.repo_config = repo_config
        self._pendientes: dict = {}
        self._cerrojo = threading.Lock()

    def anotar(self, clase: str, tarea: str, tipo: str, registro: dict,
               forzar: bool = False) -> None:
        with self._cerrojo:
            self._pendientes[(clase, tarea, registro["owner"].lower())] = (tipo, registro, forzar)

    def pendientes(self) -> int:
        with self._cerrojo:
            return len(self._pendientes)

    def guardar(self, intentos: int = 6) -> dict:
        """Escribe lo pendiente. Devuelve {(clase, tarea, usuario): "nueva"|"actualizada"|...}."""
        with self._cerrojo:
            pendientes, self._pendientes = self._pendientes, {}
        por_clase: dict = {}
        for (clase, tarea, usuario), valor in pendientes.items():
            por_clase.setdefault(clase, []).append((tarea, usuario, valor))
        estados: dict = {}
        for clase, notas in por_clase.items():
            ruta = f"{clase}/scores.json"
            for intento in range(intentos):
                try:
                    try:
                        texto, sha = self.gh.leer_fichero(self.organizacion, self.repo_config, ruta)
                        scores = json.loads(texto)
                    except ErrorGitHub as exc:
                        if exc.estado != 404:
                            raise
                        scores, sha = {"schema": "classroom50/scores/v1", "assignments": {}}, None
                    resumen = {}
                    for tarea, usuario, (tipo, registro, forzar) in notas:
                        resumen[(clase, tarea, usuario)] = aplicar_nota(scores, tarea, tipo,
                                                                        registro, forzar)
                    cambios = [k for k, v in resumen.items() if v != "respetada"]
                    if cambios:
                        mensaje = (f"Corrector: {len(cambios)} nota(s) en {clase} "
                                   f"({', '.join(sorted({k[1] for k in cambios}))}) [skip ci]")
                        self.gh.escribir_fichero(
                            self.organizacion, self.repo_config, ruta,
                            json.dumps(scores, indent=2, ensure_ascii=False) + "\n", mensaje, sha)
                    estados.update(resumen)
                    break
                except Conflicto:
                    log.info("scores.json de %s cambió mientras escribía; reintento (%d)",
                             clase, intento + 1)
            else:
                # No se pierden: vuelven a la cola para el siguiente intento.
                with self._cerrojo:
                    for tarea, usuario, valor in notas:
                        self._pendientes.setdefault((clase, tarea, usuario), valor)
                raise ErrorGitHub(409, f"no se pudo escribir {ruta} tras {intentos} intentos")
        return estados


def tarde(fecha: str, limite: Optional[str]) -> Optional[bool]:
    if not limite:
        return None
    a = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
    b = datetime.fromisoformat(limite.replace("Z", "+00:00"))
    return a > b
