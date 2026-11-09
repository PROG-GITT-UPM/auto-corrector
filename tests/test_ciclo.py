"""El ciclo completo contra un GitHub falso: listar entregas, corregir, publicar el Release y
guardar la nota en scores.json como lo haría la web de Classroom 50."""
import json
import os
from pathlib import Path

import pytest

from corrector import config, publicar
from corrector.corregir import Contexto, corregir_entrega

from github_falso import GitHubFalso

ORG = "PROG-GITT-UPM"
RAIZ = Path(__file__).resolve().parents[2]
PRACTICA = RAIZ / "prog-lab-02"
SOLUCION = Path(__file__).parent / "fixtures" / "lab_02" / "solucion"
ESQUEMAS = os.environ.get("CLASSROOM50_ESQUEMAS")  # carpeta schemas/ de un clon de classroom50


def ficheros_practica() -> dict:
    return {p.relative_to(PRACTICA).as_posix(): p.read_bytes()
            for p in PRACTICA.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    gh = GitHubFalso(ORG)
    plantilla = ficheros_practica()
    gh.commit("prog-lab-02", plantilla, "2026-11-01T10:00:00Z")
    gh.poner_fichero("classroom50", "grupo-test/assignments.json", json.dumps({
        "schema": "classroom50/assignments/v1",
        "assignments": [
            {"slug": "lab-02-prueba", "name": "Lab 2", "mode": "individual",
             "autograder": "default", "no_autograder": True,
             "grading": {"mode": "manual", "max_points": 9}, "due": "2026-11-05T22:00:00Z"},
            {"slug": "lab-02-prueba-b", "name": "Otra", "mode": "individual",
             "autograder": "default"},
        ]}))
    gh.poner_fichero("classroom50", "grupo-test/scores.json", json.dumps({
        "schema": "classroom50/scores/v1",
        "assignments": {"factorial-test": {"type": "individual", "entries": [
            {"owner": "hugo", "submissions": [{
                "schema": "classroom50/result/v1", "classroom": "grupo-test",
                "assignment_type": "individual", "owner": "hugo",
                "submission": "submit/2026-10-12T10-00-00Z-abcdef1", "commit": "c", "release": "r",
                "review": "v", "datetime": "2026-10-12T10:00:00Z", "score": 1, "max-score": 1,
                "tests": []}]}]}}}))

    # ana: todo resuelto. luis: plantilla sin tocar y unas pruebas «retocadas» que pasan siempre.
    # pepe: es de otra tarea cuyo nombre empieza igual.
    ana = {**plantilla, **{f"{p.name}": p.read_bytes() for p in SOLUCION.iterdir()}}
    gh.commit("grupo-test-lab-02-prueba-ana", ana, "2026-11-03T18:00:00Z")
    trampa = {**plantilla, ".corrector/pruebas.json": json.dumps(
        [{"name": "siempre", "type": "run", "run": "true", "points": 9}]).encode()}
    gh.commit("grupo-test-lab-02-prueba-luis", trampa, "2026-11-04T09:00:00Z")
    gh.commit("grupo-test-lab-02-prueba-b-pepe", plantilla, "2026-11-04T10:00:00Z")

    toml = tmp_path / "corrector.toml"
    toml.write_text(f'''
[github]
organizacion = "{ORG}"
api = "{gh.url}"
[sandbox]
tipo = "local"
inseguro = true
[trabajo]
carpeta = "{tmp_path / 'trabajo'}"
[tareas."grupo-test/lab-02-prueba"]
plantilla = "{ORG}/prog-lab-02"
''', encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "token-de-prueba")
    ctx = Contexto(config.cargar(toml))
    yield gh, ctx, ctx.cfg.tarea("grupo-test", "lab-02-prueba")
    gh.cerrar()


def test_ciclo_completo(entorno):
    gh, ctx, tarea = entorno
    usuarios = [u for u, _, _ in ctx.repos.listar_repos(tarea)]
    assert sorted(usuarios) == ["ana", "luis"]          # pepe es de lab-02-prueba-b

    r_ana = corregir_entrega(ctx, tarea, "ana")
    r_luis = corregir_entrega(ctx, tarea, "luis")
    assert (r_ana["puntos"], r_ana["max_puntos"]) == (9, 9)
    assert r_luis["puntos"] == 0                         # sus pruebas retocadas no cuentan
    assert r_ana["tarde"] is False

    # Release en el repositorio del alumno, en el commit corregido, con result.json adjunto.
    [rel] = gh.releases["grupo-test-lab-02-prueba-ana"]
    assert rel["tag_name"].startswith("correccion/") and rel["name"].startswith("Corrección: 9/9")
    assert "Todas las pruebas superadas" in rel["body"]
    resultado = json.loads(gh.assets[rel["id"]]["result.json"])
    assert resultado["schema"] == "classroom50/result/v1" and resultado["owner"] == "ana"
    assert resultado["release"] == rel["html_url"]
    [rel_luis] = gh.releases["grupo-test-lab-02-prueba-luis"]
    assert "❌" in rel_luis["body"] and "Qué ha fallado" in rel_luis["body"]

    # Las notas se escriben juntas, en un solo commit con [skip ci].
    assert gh.commits_contenido == []
    estados = ctx.notas.guardar()
    assert set(estados.values()) == {"nueva"}
    assert len(gh.commits_contenido) == 1 and "[skip ci]" in gh.commits_contenido[0]
    scores = json.loads(gh.leer("classroom50", "grupo-test/scores.json"))
    assert "factorial-test" in scores["assignments"]     # lo que había se conserva
    entradas = {e["owner"]: e for e in scores["assignments"]["lab-02-prueba"]["entries"]}
    assert entradas["ana"]["override"] is True
    reg = entradas["ana"]["submissions"][0]
    assert reg["score"] == 9 and reg["submission"].startswith("submit/manual-corrector-")
    assert "assignment" not in reg and reg["late"] is False

    if ESQUEMAS:
        jsonschema = pytest.importorskip("jsonschema")
        esq = Path(ESQUEMAS)
        jsonschema.validate(resultado, json.loads((esq / "result-v1.schema.json").read_text()))
        jsonschema.validate(scores, json.loads((esq / "scores-v1.schema.json").read_text()))


def test_no_repite_lo_ya_corregido(entorno):
    gh, ctx, tarea = entorno
    corregir_entrega(ctx, tarea, "ana")
    ctx.notas.guardar()
    segunda = corregir_entrega(ctx, tarea, "ana")
    assert segunda["estado"] == "ya corregida"
    assert len(gh.releases["grupo-test-lab-02-prueba-ana"]) == 1
    assert ctx.notas.pendientes() == 0


def test_respeta_la_nota_manual_del_profesor(entorno):
    gh, ctx, tarea = entorno
    corregir_entrega(ctx, tarea, "luis")
    ctx.notas.guardar()
    # El profesor le pone un 5 a mano en la web de Classroom 50.
    scores = json.loads(gh.leer("classroom50", "grupo-test/scores.json"))
    entrada = scores["assignments"]["lab-02-prueba"]["entries"][0]
    entrada["submissions"].insert(0, {**entrada["submissions"][0], "score": 5,
                                      "submission": "submit/manual-2026-11-06T10-00-00Z"})
    gh.poner_fichero("classroom50", "grupo-test/scores.json", json.dumps(scores))
    # Luis sube otra versión, fuera de plazo.
    gh.commit("grupo-test-lab-02-prueba-luis",
              {**ficheros_practica(), "ejercicio_04_palindromo.py":
               (SOLUCION / "ejercicio_04_palindromo.py").read_bytes()}, "2026-11-07T09:00:00Z")
    r = corregir_entrega(ctx, tarea, "luis")
    assert r["puntos"] == 2 and r["tarde"] is True
    assert ctx.notas.guardar()[("grupo-test", "lab-02-prueba", "luis")] == "respetada"
    scores = json.loads(gh.leer("classroom50", "grupo-test/scores.json"))
    assert scores["assignments"]["lab-02-prueba"]["entries"][0]["submissions"][0]["score"] == 5


def test_hasta_corrige_lo_entregado_en_plazo(entorno):
    gh, ctx, tarea = entorno
    gh.commit("grupo-test-lab-02-prueba-ana", ficheros_practica(), "2026-11-08T09:00:00Z")
    tardia = corregir_entrega(ctx, tarea, "ana")
    en_plazo = corregir_entrega(ctx, tarea, "ana", hasta="2026-11-05T22:00:00Z")
    assert (tardia["puntos"], en_plazo["puntos"]) == (0, 9)
    ctx.notas.guardar()
    scores = json.loads(gh.leer("classroom50", "grupo-test/scores.json"))
    [entrada] = scores["assignments"]["lab-02-prueba"]["entries"]
    assert entrada["submissions"][0]["score"] == 9       # la de plazo queda como nota


def test_conflicto_al_escribir_reintenta(entorno, monkeypatch):
    gh, ctx, tarea = entorno
    corregir_entrega(ctx, tarea, "ana")
    escribir = ctx.gh.escribir_fichero
    llamadas = []

    def con_carrera(*a, **k):
        if not llamadas:   # justo antes de nuestro primer commit, otro escribe scores.json
            llamadas.append(1)
            gh.poner_fichero("classroom50", "grupo-test/scores.json",
                             gh.leer("classroom50", "grupo-test/scores.json") + " ")
        return escribir(*a, **k)

    monkeypatch.setattr(ctx.gh, "escribir_fichero", con_carrera)
    assert ctx.notas.guardar()[("grupo-test", "lab-02-prueba", "ana")] == "nueva"


def test_aplicar_nota_sobre_entrega_autocorregida():
    scores = {"schema": "classroom50/scores/v1", "assignments": {"t": {"type": "individual",
              "entries": [{"owner": "Ana", "submissions": [
                  {"submission": "submit/2026-01-01T00-00-00Z-abc", "datetime": "2026-01-01T00:00:00Z",
                   "score": 1}]}]}}}
    reg = {"owner": "ana", "submission": publicar.PREFIJO_ENVIO + "x",
           "datetime": "2026-01-02T00:00:00Z", "score": 3, "assignment": "t"}
    assert publicar.aplicar_nota(scores, "t", "individual", reg) == "actualizada"
    entrada = scores["assignments"]["t"]["entries"][0]
    assert entrada["override"] is True and [s["score"] for s in entrada["submissions"]] == [3, 1]
