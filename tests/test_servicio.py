"""El servicio: vigilancia de pushes, cola, API HTTP con token y guardado de notas por lotes."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from corrector.servicio import Servicio, _manejador

from test_ciclo import entorno  # noqa: F401  (fixture compartida)


def esperar(condicion, segundos=20):
    limite = time.time() + segundos
    while time.time() < limite:
        if condicion():
            return True
        time.sleep(0.1)
    return False


def test_vigilancia_corrige_y_guarda(entorno):  # noqa: F811
    gh, ctx, tarea = entorno
    ctx.cfg.servicio = {"vigilar": [tarea.clave], "vigilar_cada": 0, "guardar_cada": 3600}
    servicio = Servicio(ctx)
    servicio.arrancar_hilos()
    try:
        assert servicio.vigilar_una_vez() == 2              # ana y luis
        assert esperar(lambda: all(t["estado"] in ("hecho", "error")
                                   for t in servicio.trabajos.values()))
        assert {t["estado"] for t in servicio.trabajos.values()} == {"hecho"}
        assert servicio.vigilar_una_vez() == 0              # sin pushes nuevos, nada
        gh.commit("grupo-test-lab-02-prueba-ana", {}, "2026-11-09T09:00:00Z")
        assert servicio.vigilar_una_vez() == 1              # solo el que ha hecho push
        assert esperar(lambda: servicio.cola.unfinished_tasks == 0)
    finally:
        servicio.detener()                                  # guarda las notas pendientes
    scores = json.loads(gh.leer("classroom50", "grupo-test/scores.json"))
    assert {e["owner"] for e in scores["assignments"]["lab-02-prueba"]["entries"]} == {"ana", "luis"}
    assert len(gh.commits_contenido) == 1                   # un solo commit para todo


def test_api_con_token(entorno):  # noqa: F811
    gh, ctx, tarea = entorno
    servicio = Servicio(ctx)
    servicio.arrancar_hilos()
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _manejador(servicio, "secreto"))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{servidor.server_address[1]}"

    def pedir(ruta, datos=None, token="secreto"):
        req = urllib.request.Request(base + ruta, method="POST" if datos is not None else "GET",
                                     data=json.dumps(datos).encode() if datos is not None else None,
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        assert pedir("/salud", token="")[0] == 200
        assert pedir("/trabajos", token="otro")[0] == 401
        assert pedir("/corregir", {"clase": "grupo-test"})[0] == 400
        codigo, resp = pedir("/corregir", {"clase": "grupo-test", "tarea": "lab-02-prueba",
                                           "alumnos": ["ana"]})
        assert codigo == 202 and len(resp["trabajos"]) == 1
        ident = resp["trabajos"][0]
        assert esperar(lambda: pedir(f"/trabajos/{ident}")[1]["estado"] == "hecho")
        assert pedir(f"/trabajos/{ident}")[1]["resumen"]["puntos"] == 9
    finally:
        servidor.shutdown()
        servicio.detener()
