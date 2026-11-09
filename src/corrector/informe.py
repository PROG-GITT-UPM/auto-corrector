"""Lo que produce una corrección: `result.json` (contrato classroom50/result/v1) y el texto del
Release que ve el alumno."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import __version__, motor
from .entregas import Entrega, Plantilla

LIMITE_CUERPO = 120_000  # GitHub admite 125 000 caracteres en el cuerpo de un Release


def ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def result_json(entrega: Entrega, plantilla: Plantilla, resultado: motor.Resultado,
                url_release: str, envio: str, tipo: str = "individual",
                corregido: Optional[str] = None) -> dict:
    """El resultado en el formato de Classroom 50 (classroom50/result/v1).

    `envio` es el identificador de la entrega. Para que la web de Classroom 50 la trate como nota
    manual (la que se puede borrar desde la web) empieza por «submit/manual-»."""
    return {
        "schema": "classroom50/result/v1",
        "classroom": entrega.clase,
        "assignment": entrega.tarea,
        "assignment_type": tipo,
        "owner": entrega.usuario,
        "submission": envio,
        "commit": entrega.url_commit,
        "release": url_release or entrega.url_commit,
        "review": entrega.url_revision,
        "datetime": entrega.fecha,
        "graded_at": corregido or ahora_iso(),
        "score": resultado.puntos,
        "max-score": resultado.max_puntos,
        "tests": [r.a_classroom50() for r in resultado.pruebas],
    }


def datos_corrector(plantilla: Plantilla) -> dict:
    """Campo propio para el registro de scores.json (allí se admiten campos extra; en result.json
    no, el esquema classroom50/result/v1 los rechaza)."""
    return {"version": __version__, "motor": motor.VERSION_MOTOR,
            "plantilla": plantilla.repo, "plantilla_sha": plantilla.sha}


def _bloque(texto: str) -> str:
    texto = motor.recortar(texto)
    valla = "````" if "```" in texto else "```"
    return f"{valla}text\n{texto}\n{valla}"


def cuerpo_release(entrega: Entrega, plantilla: Plantilla, resultado: motor.Resultado,
                   restaurados: list) -> str:
    """El texto del Release, en español y pensado para un alumno de primero."""
    p, m = resultado.puntos, resultado.max_puntos
    titulo = "Todas las pruebas superadas" if p == m and m else f"{p} de {m} puntos"
    lineas = [
        f"## {titulo}",
        "",
        f"Corrección del commit [`{entrega.sha7}`]({entrega.url_commit}) "
        f"({entrega.fecha.replace('T', ' ').replace('Z', ' UTC')}).",
        "",
        "| | Prueba | Puntos |",
        "|---|---|---|",
    ]
    for r in resultado.pruebas:
        marca = "✅" if r.superada else "❌"
        lineas.append(f"| {marca} | {r.nombre} | {r.puntos}/{r.max_puntos} |")
    fallos = [r for r in resultado.pruebas if not r.superada]
    if fallos:
        lineas += ["", "### Qué ha fallado", "",
                   "Puedes repetir cada prueba en tu ordenador con `python comprobar.py`."]
        for r in fallos:
            lineas += ["", "<details>", f"<summary><b>{r.nombre}</b>: {r.motivo}</summary>", ""]
            if r.tipo == "io":
                if r.entrada:
                    lineas += ["Lo que se escribe en el teclado:", "", _bloque(r.entrada), ""]
                diferencia = motor.explicar_diferencia(r.esperado, r.salida) if r.salida else ""
                if diferencia:
                    lineas += ["Dónde difiere:", "", _bloque(diferencia), ""]
                lineas += ["Salida esperada:", "", _bloque(r.esperado), "",
                           "Tu salida:", "", _bloque(r.salida or "(nada)"), ""]
                if r.comparacion == "included":
                    lineas += ["Tu salida puede llevar más texto (por ejemplo, los mensajes de "
                               "`input()`), pero tiene que contener la salida esperada tal cual, "
                               "con los mismos espacios.", ""]
            elif r.salida:
                lineas += ["Salida:", "", _bloque(r.salida), ""]
            if r.error.strip():
                lineas += ["Errores del programa:", "", _bloque(r.error), ""]
            lineas.append("</details>")
    if restaurados:
        lineas += ["", f"Para corregir se han usado los ficheros originales de la práctica en "
                       f"lugar de los tuyos: {', '.join(f'`{f}`' for f in restaurados)}."]
    lineas += ["", "---",
               f"<sub>Corregido en los servidores de la asignatura · pruebas de "
               f"`{plantilla.repo}@{plantilla.sha7}` · corrector {__version__}</sub>"]
    cuerpo = "\n".join(lineas)
    if len(cuerpo) > LIMITE_CUERPO:
        cuerpo = cuerpo[:LIMITE_CUERPO] + "\n\n… (informe recortado: es demasiado largo)"
    return cuerpo
