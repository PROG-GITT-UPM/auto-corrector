"""Una corrección de principio a fin: entrega → carpeta preparada → pruebas en el contenedor →
Release para el alumno → nota pendiente de guardar en Classroom 50."""
from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import informe, motor, publicar
from .config import Config, Tarea
from .entregas import Repositorios, preparar_carpeta
from .github import GitHub
from .sandbox import crear_sandbox

log = logging.getLogger(__name__)


class ErrorCorreccion(RuntimeError):
    pass


@dataclass
class Contexto:
    cfg: Config
    gh: GitHub = field(init=False)
    repos: Repositorios = field(init=False)
    notas: publicar.Calificaciones = field(init=False)

    def __post_init__(self) -> None:
        self.gh = GitHub(self.cfg.token, api=self.cfg.api, web=self.cfg.web)
        self.repos = Repositorios(self.gh, self.cfg)
        self.notas = publicar.Calificaciones(self.gh, self.cfg.organizacion, self.cfg.repo_config)


def pruebas_de(carpeta: Path, tarea: Tarea, practica: dict) -> list:
    """Las pruebas de la plantilla (las que ve el alumno) más las ocultas, si hay."""
    corrector = carpeta / tarea.carpeta_corrector
    pruebas = motor.cargar_pruebas(corrector / practica.get("pruebas", "pruebas.json"))
    if tarea.pruebas_extra:
        nombres = {p.nombre for p in pruebas}
        extra = motor.cargar_pruebas(tarea.pruebas_extra)
        repetidas = nombres & {p.nombre for p in extra}
        if repetidas:
            raise ErrorCorreccion(f"pruebas con el mismo nombre en las ocultas: {repetidas}")
        pruebas += extra
    return pruebas


def corregir_entrega(ctx: Contexto, tarea: Tarea, usuario: str, hasta: Optional[str] = None,
                     forzar: bool = False, publicar_resultado: bool = True) -> dict:
    """Corrige la entrega de un alumno. Devuelve un resumen (para el registro o la API)."""
    cfg = ctx.cfg
    datos_tarea = ctx.repos.tarea_classroom(tarea.clase, tarea.tarea)
    tipo = datos_tarea.get("mode", "individual")
    if tipo != "individual":
        raise ErrorCorreccion(f"{tarea.clave} es una tarea {tipo}: de momento el corrector solo "
                              "corrige tareas individuales")
    if datos_tarea.get("no_autograder") is not True:
        log.warning("%s no tiene «Do not use the built-in autograder»: cada push gastará minutos "
                    "de Actions con el corrector de Classroom 50", tarea.clave)

    entrega = ctx.repos.resolver(tarea, usuario, hasta)
    plantilla = ctx.repos.plantilla(tarea)
    huella = publicar.huella_carpeta(plantilla.carpeta / tarea.carpeta_corrector)
    if tarea.pruebas_extra:
        huella = hashlib.sha256(
            (huella + tarea.pruebas_extra.read_text(encoding="utf-8")).encode()).hexdigest()[:16]
    resumen = {"clase": tarea.clase, "tarea": tarea.tarea, "usuario": usuario,
               "repo": entrega.repo, "commit": entrega.sha7, "fecha": entrega.fecha}

    if publicar_resultado and tarea.publicar_release and not forzar:
        previo = publicar.release_existente(ctx.gh, entrega, huella)
        if previo:
            return {**resumen, "estado": "ya corregida", "release": previo["html_url"]}

    carpeta = cfg.trabajo / "entregas" / f"{entrega.repo}-{entrega.sha7}-{uuid.uuid4().hex[:6]}"
    try:
        ctx.repos.descargar(entrega, carpeta)
        restaurados = preparar_carpeta(carpeta, plantilla, tarea.carpeta_corrector)
        pruebas = pruebas_de(carpeta, tarea, plantilla.practica)
        log.info("corrigiendo %s@%s (%d pruebas)", entrega.repo, entrega.sha7, len(pruebas))
        with crear_sandbox(cfg.sandbox, carpeta) as caja:
            resultado = motor.evaluar(pruebas, caja.ejecutar, carpeta)
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)

    sello = informe.ahora_iso()
    envio = publicar.PREFIJO_ENVIO + sello.replace(":", "-")
    datos = informe.result_json(entrega, plantilla, resultado, url_release="", envio=envio,
                                tipo=tipo, corregido=sello)
    es_tarde = publicar.tarde(entrega.fecha, datos_tarea.get("due"))
    resumen.update(estado="corregida", puntos=resultado.puntos, max_puntos=resultado.max_puntos,
                   tarde=es_tarde)
    if not publicar_resultado:
        resumen["resultado"] = resultado.a_dict()
        return resumen

    if tarea.publicar_release:
        cuerpo = informe.cuerpo_release(entrega, plantilla, resultado, restaurados)
        resumen["release"] = publicar.publicar_release(ctx.gh, entrega, cuerpo, datos, huella, sello)
    if tarea.guardar_nota:
        registro = {**datos, "corrector": informe.datos_corrector(plantilla)}
        if es_tarde is not None:
            registro["late"] = es_tarde
        ctx.notas.anotar(tarea.clase, tarea.tarea, tipo, registro, forzar=forzar)
        resumen["nota"] = "pendiente de guardar"
    return resumen
