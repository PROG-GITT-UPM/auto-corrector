"""Cliente mínimo de la API REST de GitHub, sin dependencias.

Ninguna llamada de este módulo lanza GitHub Actions: lectura de repositorios, descarga de código,
creación de Releases y escritura de ficheros con la API de contenidos.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)


class ErrorGitHub(RuntimeError):
    def __init__(self, estado: int, mensaje: str, url: str = "") -> None:
        super().__init__(f"GitHub {estado} en {url}: {mensaje}")
        self.estado = estado
        self.mensaje = mensaje


class Conflicto(ErrorGitHub):
    """El fichero cambió entre la lectura y la escritura (otro commit llegó antes)."""


class GitHub:
    def __init__(self, token: str, api: str = "https://api.github.com",
                 web: str = "https://github.com", reintentos: int = 4) -> None:
        if not token:
            raise ValueError("falta el token de GitHub")
        self.token = token
        self.api = api.rstrip("/")
        self.web = web.rstrip("/")
        self.reintentos = reintentos

    # ─── Peticiones ──────────────────────────────────────────────────────────

    def _peticion(self, metodo: str, url: str, cuerpo: Optional[bytes] = None,
                  tipo: str = "application/json",
                  aceptar: str = "application/vnd.github+json") -> tuple[int, dict, bytes]:
        if not url.startswith("http"):
            url = f"{self.api}{url}"
        cabeceras = {
            "Authorization": f"Bearer {self.token}",
            "Accept": aceptar,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "corrector-prog",
        }
        if cuerpo is not None:
            cabeceras["Content-Type"] = tipo
        espera = 2.0
        for intento in range(self.reintentos + 1):
            req = urllib.request.Request(url, data=cuerpo, method=metodo, headers=cabeceras)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.status, dict(resp.headers), resp.read()
            except urllib.error.HTTPError as exc:
                datos = exc.read()
                estado = exc.code
                restantes = exc.headers.get("x-ratelimit-remaining")
                if estado in (403, 429) and (restantes == "0" or exc.headers.get("retry-after")):
                    reinicio = exc.headers.get("retry-after")
                    pausa = float(reinicio) if reinicio else max(
                        1.0, float(exc.headers.get("x-ratelimit-reset", time.time() + 60)) - time.time())
                    log.warning("límite de la API de GitHub; espero %.0f s", pausa)
                    time.sleep(min(pausa, 900))
                    continue
                if estado >= 500 and intento < self.reintentos:
                    time.sleep(espera)
                    espera *= 2
                    continue
                try:
                    mensaje = json.loads(datos).get("message", "")
                except (json.JSONDecodeError, AttributeError):
                    mensaje = datos[:200].decode("utf-8", "replace")
                clase = Conflicto if estado == 409 or (
                    estado == 422 and "sha" in mensaje.lower()) else ErrorGitHub
                raise clase(estado, mensaje, url) from None
            except urllib.error.URLError as exc:
                if intento < self.reintentos:
                    time.sleep(espera)
                    espera *= 2
                    continue
                raise ErrorGitHub(0, str(exc.reason), url) from None
        raise ErrorGitHub(0, "demasiados reintentos", url)

    def get(self, ruta: str, **params: Any) -> Any:
        if params:
            ruta = f"{ruta}?{urllib.parse.urlencode(params)}"
        _, _, datos = self._peticion("GET", ruta)
        return json.loads(datos) if datos else None

    def enviar(self, metodo: str, ruta: str, datos: Any) -> Any:
        _, _, resp = self._peticion(metodo, ruta, json.dumps(datos).encode("utf-8"))
        return json.loads(resp) if resp else None

    def paginar(self, ruta: str, **params: Any) -> Iterator[Any]:
        params.setdefault("per_page", 100)
        url: Optional[str] = f"{ruta}?{urllib.parse.urlencode(params)}"
        while url:
            _, cabeceras, datos = self._peticion("GET", url)
            yield from json.loads(datos)
            enlaces = cabeceras.get("Link") or cabeceras.get("link") or ""
            siguiente = re.search(r'<([^>]+)>;\s*rel="next"', enlaces)
            url = siguiente.group(1) if siguiente else None

    # ─── Operaciones ─────────────────────────────────────────────────────────

    def descargar_tarball(self, propietario: str, repo: str, ref: str) -> bytes:
        _, _, datos = self._peticion("GET", f"/repos/{propietario}/{repo}/tarball/{ref}")
        return datos

    def leer_fichero(self, propietario: str, repo: str, ruta: str,
                     ref: Optional[str] = None) -> tuple[str, str]:
        """(contenido, sha del blob). Usa la API de blobs si el fichero pasa de 1 MB."""
        params = {"ref": ref} if ref else {}
        info = self.get(f"/repos/{propietario}/{repo}/contents/{urllib.parse.quote(ruta)}", **params)
        if info.get("encoding") == "base64" and info.get("content"):
            return base64.b64decode(info["content"]).decode("utf-8"), info["sha"]
        blob = self.get(f"/repos/{propietario}/{repo}/git/blobs/{info['sha']}")
        return base64.b64decode(blob["content"]).decode("utf-8"), info["sha"]

    def escribir_fichero(self, propietario: str, repo: str, ruta: str, contenido: str,
                         mensaje: str, sha: Optional[str]) -> dict:
        """Un commit con el fichero. Lanza `Conflicto` si `sha` ya no es el actual."""
        datos: dict = {"message": mensaje,
                       "content": base64.b64encode(contenido.encode("utf-8")).decode("ascii")}
        if sha:
            datos["sha"] = sha
        return self.enviar("PUT", f"/repos/{propietario}/{repo}/contents/{urllib.parse.quote(ruta)}",
                           datos)

    def subir_asset(self, upload_url: str, nombre: str, datos: bytes,
                    tipo: str = "application/json") -> dict:
        url = re.sub(r"\{.*\}$", "", upload_url) + "?" + urllib.parse.urlencode({"name": nombre})
        _, _, resp = self._peticion("POST", url, datos, tipo=tipo)
        return json.loads(resp)
