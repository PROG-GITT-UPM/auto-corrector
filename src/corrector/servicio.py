"""El corrector como servicio siempre encendido.

* Una **API HTTP** para pedir correcciones (`POST /corregir`) y consultar cómo van.
* Una **cola** con N trabajadores: cada entrega se corrige en su propio contenedor.
* **Vigilancia** (opcional): cada `vigilar_cada` segundos mira qué repositorios de las tareas
  vigiladas han recibido un push y los mete en la cola. Basta una consulta paginada a la
  organización por vuelta, así que no gasta el límite de la API, y no necesita que GitHub pueda
  llegar al servidor (no hace falta webhook ni abrir puertos).
* Las notas se guardan en Classroom 50 en lotes, cada `guardar_cada` segundos.

Solo biblioteca estándar. Pensado para ir detrás de un proxy (nginx) o en la red interna.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import signal
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .corregir import Contexto, corregir_entrega

log = logging.getLogger(__name__)


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Servicio:
    def __init__(self, ctx: Contexto) -> None:
        self.ctx = ctx
        conf = ctx.cfg.servicio
        self.trabajadores = int(conf.get("trabajadores", 2))
        self.vigilar = list(conf.get("vigilar", []))
        self.vigilar_cada = int(conf.get("vigilar_cada", 120))
        self.guardar_cada = int(conf.get("guardar_cada", 30))
        self.cola: queue.Queue = queue.Queue()
        self.trabajos: OrderedDict = OrderedDict()
        self.en_cola: set = set()
        self.cerrojo = threading.Lock()
        self.parar = threading.Event()
        self.estado_vigilancia = ctx.cfg.trabajo / "vigilancia.json"

    # ─── Trabajos ────────────────────────────────────────────────────────────

    def encolar(self, clase: str, tarea: str, usuario: str, forzar: bool = False,
                hasta: Optional[str] = None, origen: str = "api") -> Optional[str]:
        """Mete una entrega en la cola. Si ya estaba esperando, no la duplica."""
        self.ctx.cfg.tarea(clase, tarea)  # error si la tarea no está configurada
        clave = (clase, tarea, usuario.lower())
        with self.cerrojo:
            if clave in self.en_cola:
                return None
            ident = uuid.uuid4().hex[:12]
            self.trabajos[ident] = {"id": ident, "clase": clase, "tarea": tarea, "usuario": usuario,
                                    "forzar": forzar, "hasta": hasta, "origen": origen,
                                    "estado": "en cola", "creado": _ahora()}
            while len(self.trabajos) > 2000:
                self.trabajos.popitem(last=False)
            self.en_cola.add(clave)
        self.cola.put(ident)
        return ident

    def _trabajador(self) -> None:
        while not self.parar.is_set():
            try:
                ident = self.cola.get(timeout=1)
            except queue.Empty:
                continue
            with self.cerrojo:
                t = self.trabajos.get(ident)
                if t is None:          # descartado por antiguo mientras esperaba
                    self.cola.task_done()
                    continue
                self.en_cola.discard((t["clase"], t["tarea"], t["usuario"].lower()))
                t["estado"] = "corrigiendo"
                t["empezado"] = _ahora()
            try:
                tarea = self.ctx.cfg.tarea(t["clase"], t["tarea"])
                resumen = corregir_entrega(self.ctx, tarea, t["usuario"], hasta=t.get("hasta"),
                                           forzar=t.get("forzar", False))
                with self.cerrojo:
                    t.update(estado="hecho", resumen=resumen, terminado=_ahora())
                log.info("%s/%s %s: %s %s", t["clase"], t["tarea"], t["usuario"],
                         resumen.get("estado"),
                         f"{resumen.get('puntos')}/{resumen.get('max_puntos')}"
                         if "puntos" in resumen else "")
            except Exception as exc:  # un fallo en una entrega no tumba el servicio
                log.exception("fallo corrigiendo %s", t["usuario"])
                with self.cerrojo:
                    t.update(estado="error", error=str(exc), terminado=_ahora())
            finally:
                self.cola.task_done()

    # ─── Notas ───────────────────────────────────────────────────────────────

    def _guardar_notas(self) -> None:
        try:
            if self.ctx.notas.pendientes():
                estados = self.ctx.notas.guardar()
                log.info("notas guardadas en Classroom 50: %d", len(estados))
                for (clase, tarea, usuario), estado in estados.items():
                    if estado == "respetada":
                        log.warning("%s/%s %s: tiene nota manual del profesor; no se ha cambiado",
                                    clase, tarea, usuario)
        except Exception:
            log.exception("no se pudieron guardar las notas; se reintentará")

    def _guardador(self) -> None:
        while not self.parar.wait(self.guardar_cada):
            self._guardar_notas()

    # ─── Vigilancia ──────────────────────────────────────────────────────────

    def _leer_estado(self) -> dict:
        try:
            return json.loads(self.estado_vigilancia.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def vigilar_una_vez(self) -> int:
        """Encola los repositorios con pushes nuevos desde la última vuelta."""
        estado = self._leer_estado()
        nuevos = 0
        for clave in self.vigilar:
            tarea = self.ctx.cfg.tarea(*clave.split("/", 1))
            for usuario, repo, pushed_at in self.ctx.repos.listar_repos(tarea):
                if pushed_at and estado.get(repo) == pushed_at:
                    continue
                # Si la entrega ya está corregida, el trabajo acaba enseguida («ya corregida»).
                if self.encolar(tarea.clase, tarea.tarea, usuario, origen="vigilancia"):
                    nuevos += 1
                estado[repo] = pushed_at
        self.estado_vigilancia.parent.mkdir(parents=True, exist_ok=True)
        temporal = self.estado_vigilancia.with_suffix(".tmp")
        temporal.write_text(json.dumps(estado, indent=1), encoding="utf-8")
        temporal.replace(self.estado_vigilancia)
        return nuevos

    def _vigilante(self) -> None:
        while not self.parar.is_set():
            try:
                n = self.vigilar_una_vez()
                if n:
                    log.info("vigilancia: %d entrega(s) nueva(s) en cola", n)
            except Exception:
                log.exception("fallo en la vigilancia; sigo en la próxima vuelta")
            self.parar.wait(self.vigilar_cada)

    # ─── Arranque ────────────────────────────────────────────────────────────

    def arrancar_hilos(self) -> None:
        for i in range(self.trabajadores):
            threading.Thread(target=self._trabajador, name=f"trabajador-{i}", daemon=True).start()
        threading.Thread(target=self._guardador, name="notas", daemon=True).start()
        if self.vigilar and self.vigilar_cada > 0:
            threading.Thread(target=self._vigilante, name="vigilancia", daemon=True).start()

    def detener(self) -> None:
        self.parar.set()
        self._guardar_notas()


def _manejador(servicio: Servicio, token_api: str):
    class Manejador(BaseHTTPRequestHandler):
        server_version = "corrector"

        def log_message(self, formato: str, *args: object) -> None:
            log.debug("%s %s", self.address_string(), formato % args)

        def _responder(self, codigo: int, datos: object) -> None:
            cuerpo = json.dumps(datos, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _autorizado(self) -> bool:
            if not token_api:
                return True
            recibido = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if hmac.compare_digest(recibido, token_api):
                return True
            self._responder(401, {"error": "falta o no vale el token (Authorization: Bearer …)"})
            return False

        def do_GET(self) -> None:
            if self.path == "/salud":
                return self._responder(200, {"ok": True, "cola": servicio.cola.qsize(),
                                             "notas_pendientes": servicio.ctx.notas.pendientes()})
            if not self._autorizado():
                return
            if self.path.startswith("/trabajos/"):
                ident = self.path.rsplit("/", 1)[1]
                with servicio.cerrojo:
                    t = servicio.trabajos.get(ident)
                return self._responder(200, t) if t else self._responder(404, {"error": "no existe"})
            if self.path.split("?")[0] == "/trabajos":
                with servicio.cerrojo:
                    ultimos = list(servicio.trabajos.values())[-100:]
                return self._responder(200, list(reversed(ultimos)))
            self._responder(404, {"error": "ruta desconocida"})

        def do_POST(self) -> None:
            if not self._autorizado():
                return
            if self.path not in ("/corregir", "/vigilar"):
                return self._responder(404, {"error": "ruta desconocida"})
            if self.path == "/vigilar":
                return self._responder(200, {"encoladas": servicio.vigilar_una_vez()})
            try:
                largo = int(self.headers.get("Content-Length", 0))
                datos = json.loads(self.rfile.read(largo) or b"{}")
                clase, tarea = datos["clase"], datos["tarea"]
                tarea_cfg = servicio.ctx.cfg.tarea(clase, tarea)
                alumnos = datos.get("alumnos") or [u for u, _, _ in
                                                   servicio.ctx.repos.listar_repos(tarea_cfg)]
                ids = [i for u in alumnos if (i := servicio.encolar(
                    clase, tarea, u, forzar=bool(datos.get("forzar")), hasta=datos.get("hasta")))]
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                return self._responder(400, {"error": f"petición no válida: {exc}"})
            self._responder(202, {"trabajos": ids, "alumnos": len(alumnos)})

    return Manejador


def servir(ctx: Contexto, anfitrion: Optional[str] = None, puerto: Optional[int] = None) -> None:
    conf = ctx.cfg.servicio
    anfitrion = anfitrion or conf.get("anfitrion", "127.0.0.1")
    puerto = int(puerto or conf.get("puerto", 8080))
    token_api = os.environ.get(conf.get("api_token_env", "CORRECTOR_API_TOKEN"), "")
    if not token_api and anfitrion not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("sin token de API el servicio solo puede escuchar en 127.0.0.1")

    servicio = Servicio(ctx)
    servicio.arrancar_hilos()
    servidor = ThreadingHTTPServer((anfitrion, puerto), _manejador(servicio, token_api))

    def _apagar(*_: object) -> None:
        log.info("apagando: guardo las notas pendientes")
        threading.Thread(target=servidor.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _apagar)
    signal.signal(signal.SIGINT, _apagar)
    log.info("corrector escuchando en http://%s:%d (vigilando: %s)", anfitrion, puerto,
             ", ".join(servicio.vigilar) or "nada")
    try:
        servidor.serve_forever()
    finally:
        servicio.detener()
        servidor.server_close()
