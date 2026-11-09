"""Línea de órdenes: `corrector <orden> --help` para el detalle de cada una.

    corrector corregir   CLASE/TAREA [ALUMNO ...] [--todos]   corrige, publica y guarda notas
    corrector servir                                            servicio con API y vigilancia
    corrector local      PRACTICA ENTREGA                       corrige una carpeta, sin GitHub
    corrector validar-practica PRACTICA --solucion DIR          comprueba una práctica nueva
    corrector sincronizar-motor PRACTICA                        copia motor.py a la práctica
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import __version__, config, motor
from .entregas import Plantilla, leer_practica, preparar_carpeta

log = logging.getLogger("corrector")


def _imprimir_resultado(resultado: motor.Resultado, detalles: bool = True) -> None:
    for r in resultado.pruebas:
        print(f"  {'✔' if r.superada else '✘'} {r.nombre}  ({r.puntos}/{r.max_puntos})")
        if not r.superada and detalles:
            print(f"      {r.motivo}")
            if r.tipo == "io" and r.salida:
                for linea in motor.explicar_diferencia(r.esperado, r.salida).splitlines():
                    print(f"      {linea}")
    print(f"  Total: {resultado.puntos}/{resultado.max_puntos}")


# ─── corregir ────────────────────────────────────────────────────────────────

def orden_corregir(args: argparse.Namespace) -> int:
    from .corregir import Contexto, corregir_entrega
    cfg = config.cargar(args.config)
    clase, nombre = args.tarea.split("/", 1)
    tarea = cfg.tarea(clase, nombre)
    ctx = Contexto(cfg)
    alumnos = list(args.alumnos)
    if args.todos:
        alumnos = [u for u, _, _ in ctx.repos.listar_repos(tarea)]
    if not alumnos:
        print("Di qué alumnos corregir o usa --todos.", file=sys.stderr)
        return 2
    print(f"Corrigiendo {len(alumnos)} entrega(s) de {tarea.clave}…", file=sys.stderr)
    resumenes, fallos = [], 0
    with ThreadPoolExecutor(max_workers=args.paralelo) as pool:
        futuros = {pool.submit(corregir_entrega, ctx, tarea, u, args.hasta, args.forzar,
                               not args.sin_publicar): u for u in alumnos}
        for futuro in as_completed(futuros):
            usuario = futuros[futuro]
            try:
                r = futuro.result()
            except Exception as exc:
                fallos += 1
                log.error("%s: %s", usuario, exc)
                r = {"usuario": usuario, "estado": "error", "error": str(exc)}
            resumenes.append(r)
            if not args.json:
                nota = f" {r['puntos']}/{r['max_puntos']}" if "puntos" in r else ""
                tarde = " (fuera de plazo)" if r.get("tarde") else ""
                print(f"{usuario}: {r['estado']}{nota}{tarde}  {r.get('release', r.get('error', ''))}")
    if not args.sin_publicar and ctx.notas.pendientes():
        estados = ctx.notas.guardar()
        respetadas = [k[2] for k, v in estados.items() if v == "respetada"]
        print(f"Notas guardadas en Classroom 50: {len(estados) - len(respetadas)}", file=sys.stderr)
        if respetadas:
            print(f"No se han cambiado (tienen nota manual del profesor; usa --forzar): "
                  f"{', '.join(respetadas)}", file=sys.stderr)
    if args.json:
        print(json.dumps(resumenes, indent=1, ensure_ascii=False))
    return 1 if fallos else 0


# ─── servir ──────────────────────────────────────────────────────────────────

def orden_servir(args: argparse.Namespace) -> int:
    from .corregir import Contexto
    from .servicio import servir
    servir(Contexto(config.cargar(args.config)), args.anfitrion, args.puerto)
    return 0


# ─── local ───────────────────────────────────────────────────────────────────

def _plantilla_local(practica: Path) -> Plantilla:
    return Plantilla(repo=str(practica), sha="local", carpeta=practica,
                     practica=leer_practica(practica / ".corrector"))


def _corregir_carpeta(practica: Path, entrega: Path, docker: str | None) -> motor.Resultado:
    from .sandbox import SandboxDocker, SandboxLocal
    plantilla = _plantilla_local(practica)
    with tempfile.TemporaryDirectory(prefix="corrector-") as tmp:
        carpeta = Path(tmp) / "entrega"
        shutil.copytree(entrega, carpeta, ignore=shutil.ignore_patterns(".git"))
        preparar_carpeta(carpeta, plantilla, ".corrector")
        pruebas = motor.cargar_pruebas(
            carpeta / ".corrector" / plantilla.practica.get("pruebas", "pruebas.json"))
        caja = SandboxDocker(carpeta, docker) if docker else SandboxLocal(carpeta, sys.executable)
        with caja:
            return motor.evaluar(pruebas, caja.ejecutar, carpeta)


def orden_local(args: argparse.Namespace) -> int:
    resultado = _corregir_carpeta(args.practica, args.entrega, args.docker)
    if args.json:
        print(json.dumps(resultado.a_dict(), indent=1, ensure_ascii=False))
    else:
        _imprimir_resultado(resultado)
    return 0


# ─── validar-practica ────────────────────────────────────────────────────────

def orden_validar(args: argparse.Namespace) -> int:
    practica: Path = args.practica
    errores = 0
    try:
        datos = leer_practica(practica / ".corrector")
        pruebas = motor.cargar_pruebas(practica / ".corrector" / datos.get("pruebas", "pruebas.json"))
    except (FileNotFoundError, motor.ErrorPruebas, ValueError) as exc:
        print(f"✘ {exc}")
        return 1
    print(f"✔ {len(pruebas)} pruebas, {sum(p.puntos for p in pruebas)} puntos")
    if (practica / ".github" / "workflows").is_dir():
        print("✘ la práctica tiene .github/workflows: cada push gastaría minutos de Actions")
        errores += 1
    motor_practica = practica / ".corrector" / "motor.py"
    motor_actual = Path(motor.__file__).read_bytes()
    if not motor_practica.is_file() or motor_practica.read_bytes() != motor_actual:
        print("✘ .corrector/motor.py no es el del corrector: corrector sincronizar-motor "
              f"{practica}")
        errores += 1

    vacia = _corregir_carpeta(practica, practica, args.docker)
    print(f"{'✔' if vacia.puntos == 0 else '⚠'} plantilla sin tocar: "
          f"{vacia.puntos}/{vacia.max_puntos} (debería ser 0)")
    if args.solucion:
        with tempfile.TemporaryDirectory() as tmp:
            junta = Path(tmp) / "solucion"
            shutil.copytree(practica, junta, ignore=shutil.ignore_patterns(".git"))
            shutil.copytree(args.solucion, junta, dirs_exist_ok=True)
            completa = _corregir_carpeta(practica, junta, args.docker)
        bien = completa.puntos == completa.max_puntos
        print(f"{'✔' if bien else '✘'} con la solución: {completa.puntos}/{completa.max_puntos}")
        if not bien:
            _imprimir_resultado(completa)
            errores += 1
    return 1 if errores else 0


# ─── sincronizar-motor ───────────────────────────────────────────────────────

def orden_sincronizar(args: argparse.Namespace) -> int:
    for practica in args.practicas:
        destino = practica / ".corrector" / "motor.py"
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(motor.__file__, destino)
        print(f"copiado {destino}")
    return 0


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(prog="corrector", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"corrector {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="orden", required=True)

    c = sub.add_parser("corregir", help="corrige entregas, publica el resultado y guarda la nota")
    c.add_argument("-c", "--config", type=Path, default=Path("corrector.toml"))
    c.add_argument("tarea", help="clase/tarea, p. ej. grupo-test/lab-02-prueba")
    c.add_argument("alumnos", nargs="*", help="usuarios de GitHub")
    c.add_argument("--todos", action="store_true", help="todos los alumnos que aceptaron la tarea")
    c.add_argument("--hasta", help="corrige el último commit anterior a esta fecha (ISO 8601)")
    c.add_argument("--forzar", action="store_true",
                   help="corrige aunque ya esté corregida y pisa notas manuales")
    c.add_argument("--sin-publicar", action="store_true",
                   help="solo corrige: ni Release ni nota (para probar)")
    c.add_argument("--paralelo", type=int, default=2, help="correcciones a la vez (2)")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=orden_corregir)

    s = sub.add_parser("servir", help="servicio con API HTTP, cola y vigilancia de pushes")
    s.add_argument("-c", "--config", type=Path, default=Path("corrector.toml"))
    s.add_argument("--anfitrion")
    s.add_argument("--puerto", type=int)
    s.set_defaults(func=orden_servir)

    lo = sub.add_parser("local", help="corrige una carpeta con las pruebas de una práctica")
    lo.add_argument("practica", type=Path, help="carpeta de la práctica (con .corrector/)")
    lo.add_argument("entrega", type=Path, help="carpeta con el código a corregir")
    lo.add_argument("--docker", metavar="IMAGEN", help="ejecutar en un contenedor con esta imagen")
    lo.add_argument("--json", action="store_true")
    lo.set_defaults(func=orden_local)

    v = sub.add_parser("validar-practica", help="comprueba una práctica antes de publicarla")
    v.add_argument("practica", type=Path)
    v.add_argument("--solucion", type=Path, help="carpeta con la solución de referencia")
    v.add_argument("--docker", metavar="IMAGEN")
    v.set_defaults(func=orden_validar)

    m = sub.add_parser("sincronizar-motor", help="copia motor.py a .corrector/ de las prácticas")
    m.add_argument("practicas", type=Path, nargs="+")
    m.set_defaults(func=orden_sincronizar)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except config.ErrorConfig as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
