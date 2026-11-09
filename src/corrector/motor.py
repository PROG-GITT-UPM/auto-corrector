"""Motor de pruebas: ejecuta las pruebas de una práctica y las puntúa.

Este fichero es autocontenido (solo biblioteca estándar, Python 3.9+) porque se usa en dos sitios:

* en el corrector, que lo ejecuta dentro de un contenedor aislado en nuestros servidores;
* en el repositorio de cada práctica (`.corrector/motor.py`), donde `comprobar.py` lo usa para que
  el alumno compruebe sus ejercicios en su ordenador antes de entregar.

Así el alumno y el corrector juzgan con el mismo código. Si cambias este fichero, copia la versión
nueva a las prácticas con `corrector sincronizar-motor <carpeta-de-la-practica>`.

Las pruebas siguen el formato de Classroom 50 (el array que acepta `gh teacher assignment add
--tests`), y la semántica es la misma que la del corrector integrado de Classroom 50 (`runner.py`):

* `io`: ejecuta `run` con `input` por la entrada estándar y compara la salida estándar con
  `expected` según `comparison`: `included` (subcadena literal), `exact` (igual salvo espacios al
  principio y al final) o `regex` (`re.search` multilínea).
* `run`: pasa si el código de salida de `run` es `exit-code` (0 si no se dice).
* `python`: ejecuta pytest y reparte los puntos entre los casos que pasan.

`setup` se ejecuta antes que `run`; si falla, la prueba falla. `timeout` vale para cada orden por
separado (10 s si no se dice).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

VERSION_MOTOR = "1.0.0"
TIMEOUT_POR_DEFECTO = 10
MAX_CARACTERES = 2000
COMPARACIONES = ("included", "exact", "regex")
TIPOS = ("io", "run", "python")
INFORME_PYTEST = ".corrector-pytest.xml"


class ErrorPruebas(ValueError):
    """El fichero de pruebas no es válido."""


@dataclass
class Prueba:
    nombre: str
    tipo: str
    run: str
    puntos: int = 0
    setup: str = ""
    entrada: str = ""
    esperado: str = ""
    comparacion: str = "included"
    timeout: int = TIMEOUT_POR_DEFECTO
    codigo_salida: int = 0


@dataclass
class Ejecucion:
    """Lo que devuelve un ejecutor tras lanzar una orden."""
    codigo: Optional[int]
    salida: str
    error: str = ""
    agotado: bool = False


# Un ejecutor lanza una orden de shell en la carpeta de la entrega: (orden, entrada, timeout).
Ejecutor = Callable[[str, Optional[str], int], Ejecucion]


@dataclass
class ResultadoPrueba:
    nombre: str
    tipo: str
    superada: bool
    puntos: int
    max_puntos: int
    motivo: str = ""
    run: str = ""
    entrada: str = ""
    esperado: str = ""
    salida: str = ""
    error: str = ""
    comparacion: str = ""

    def a_classroom50(self) -> dict:
        """Una fila de `tests` de result.json (classroom50/result/v1); los campos extra se conservan."""
        fila: dict = {
            "test-name": self.nombre,
            "passed": self.superada,
            "score": self.puntos,
            "max-score": self.max_puntos,
        }
        if not self.superada and self.motivo:
            fila["detail"] = self.motivo
        return fila


@dataclass
class Resultado:
    pruebas: list = field(default_factory=list)

    @property
    def puntos(self) -> int:
        return sum(r.puntos for r in self.pruebas)

    @property
    def max_puntos(self) -> int:
        return sum(r.max_puntos for r in self.pruebas)

    def a_dict(self) -> dict:
        return {"puntos": self.puntos, "max_puntos": self.max_puntos,
                "pruebas": [asdict(r) for r in self.pruebas]}


# ─── Carga de pruebas ──────────────────────────────────────────────────────────

def _texto_o_fichero(spec: dict, clave: str, base: Path) -> str:
    """El valor de `clave` o el contenido de `clave-file` (relativo al fichero de pruebas)."""
    if spec.get(clave) is not None and spec.get(f"{clave}-file") is not None:
        raise ErrorPruebas(f"«{spec['name']}»: {clave} y {clave}-file no pueden ir juntos")
    if spec.get(f"{clave}-file") is not None:
        ruta = (base / spec[f"{clave}-file"]).resolve()
        if base.resolve() not in ruta.parents and ruta != base.resolve():
            raise ErrorPruebas(f"«{spec['name']}»: {clave}-file sale de la carpeta de las pruebas")
        if not ruta.is_file():
            raise ErrorPruebas(f"«{spec['name']}»: no existe {spec[f'{clave}-file']}")
        return normalizar(ruta.read_text(encoding="utf-8"))
    return spec.get(clave) or ""


def leer_pruebas(datos: object, base: Path) -> list:
    """Valida un array de pruebas de Classroom 50 (o un objeto con `tests`) y lo convierte."""
    if isinstance(datos, dict) and "tests" in datos:
        datos = datos["tests"]
    if not isinstance(datos, list):
        raise ErrorPruebas("el fichero de pruebas debe ser un array de pruebas")
    pruebas: list = []
    nombres: set = set()
    for i, spec in enumerate(datos, start=1):
        if not isinstance(spec, dict):
            raise ErrorPruebas(f"la prueba {i} no es un objeto")
        nombre = spec.get("name")
        if not isinstance(nombre, str) or not nombre.strip():
            raise ErrorPruebas(f"la prueba {i} no tiene nombre")
        if nombre in nombres:
            raise ErrorPruebas(f"hay dos pruebas que se llaman «{nombre}»")
        nombres.add(nombre)
        tipo = spec.get("type")
        if tipo not in TIPOS:
            raise ErrorPruebas(f"«{nombre}»: type debe ser uno de {TIPOS}")
        if not isinstance(spec.get("run"), str) or not spec["run"].strip():
            raise ErrorPruebas(f"«{nombre}»: falta run")
        puntos = spec.get("points", 0)
        if isinstance(puntos, bool) or not isinstance(puntos, int) or puntos < 0:
            raise ErrorPruebas(f"«{nombre}»: points debe ser un entero no negativo")
        timeout = spec.get("timeout", 0)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 <= timeout <= 600:
            raise ErrorPruebas(f"«{nombre}»: timeout debe estar entre 0 y 600")
        comparacion = spec.get("comparison", "included")
        if tipo == "io" and comparacion not in COMPARACIONES:
            raise ErrorPruebas(f"«{nombre}»: comparison debe ser uno de {COMPARACIONES}")
        pruebas.append(Prueba(
            nombre=nombre, tipo=tipo, run=spec["run"], puntos=puntos,
            setup=spec.get("setup") or "",
            entrada=_texto_o_fichero(spec, "input", base),
            esperado=_texto_o_fichero(spec, "expected", base),
            comparacion=comparacion,
            timeout=timeout or TIMEOUT_POR_DEFECTO,
            codigo_salida=int(spec.get("exit-code") or 0),
        ))
    return pruebas


def cargar_pruebas(ruta: Path) -> list:
    """Lee un fichero de pruebas (JSON). Las rutas `*-file` son relativas a su carpeta."""
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorPruebas(f"{ruta}: JSON no válido ({exc})") from exc
    return leer_pruebas(datos, Path(ruta).parent)


# ─── Comparación ───────────────────────────────────────────────────────────────

def normalizar(texto: Optional[str]) -> str:
    """Saltos de línea de Windows o Mac antiguo → \\n, como hace Classroom 50."""
    return (texto or "").replace("\r\n", "\n").replace("\r", "\n")


def comparar(salida: str, esperado: str, comparacion: str) -> bool:
    if comparacion == "included":
        return esperado in salida
    if comparacion == "exact":
        return salida.strip() == esperado.strip()
    if comparacion == "regex":
        return re.search(esperado, salida, re.MULTILINE) is not None
    raise ValueError(f"comparación desconocida: {comparacion}")


def recortar(texto: str, limite: int = MAX_CARACTERES) -> str:
    return texto if len(texto) <= limite else texto[:limite] + "\n… (recortado)"


def _visible(linea: str) -> str:
    """Hace visibles los espacios y tabuladores para que se vea dónde difiere una línea."""
    return linea.replace(" ", "·").replace("\t", "→")


def explicar_diferencia(esperado: str, salida: str) -> str:
    """Dónde difiere la salida de lo esperado, línea a línea, con los espacios a la vista.

    Busca en la salida el trozo que más se parece a lo esperado (la salida suele llevar delante los
    mensajes de `input()`, que no están en lo esperado) y señala la primera línea distinta y la
    primera columna en la que difiere."""
    lineas_esp = esperado.strip("\n").split("\n")
    lineas_sal = salida.split("\n")
    if not lineas_esp or not esperado.strip():
        return ""
    # La ventana de la salida, del mismo tamaño que lo esperado, que más se le parece.
    mejor, mejor_inicio = -1.0, 0
    tam = len(lineas_esp)
    for inicio in range(max(1, len(lineas_sal) - tam + 1)):
        ventana = lineas_sal[inicio:inicio + tam]
        parecido = difflib.SequenceMatcher(None, "\n".join(lineas_esp), "\n".join(ventana)).ratio()
        if parecido > mejor:
            mejor, mejor_inicio = parecido, inicio
    ventana = lineas_sal[mejor_inicio:mejor_inicio + tam]
    for n, linea_esp in enumerate(lineas_esp):
        linea_sal = ventana[n] if n < len(ventana) else None
        if linea_sal is None:
            return (f"Falta la línea {n + 1} de lo esperado:\n"
                    f"  esperado: {_visible(linea_esp)}\n  tu salida termina antes.")
        if linea_sal != linea_esp:
            col = next((i for i, (a, b) in enumerate(zip(linea_esp, linea_sal)) if a != b),
                       min(len(linea_esp), len(linea_sal)))
            return ("Primera línea distinta (· es un espacio):\n"
                    f"  esperado:  {_visible(linea_esp)}\n"
                    f"  tu salida: {_visible(linea_sal)}\n"
                    f"             {' ' * col}^ difiere en la columna {col + 1}")
    return "Las líneas coinciden, pero no seguidas o con líneas de más entre ellas."


# ─── Ejecución ─────────────────────────────────────────────────────────────────

def _evaluar_python(prueba: Prueba, ejecutar: Ejecutor, directorio: Path) -> ResultadoPrueba:
    """pytest con informe JUnit (incluido en pytest, sin plugins) y reparto de puntos por casos."""
    informe = directorio / INFORME_PYTEST
    if informe.exists():
        informe.unlink()
    orden = prueba.run
    if "--junitxml" not in orden:
        orden = f"{orden} -p no:cacheprovider --junitxml={shlex.quote(INFORME_PYTEST)}"
    ej = ejecutar(orden, None, prueba.timeout)
    base = dict(nombre=prueba.nombre, tipo="python", max_puntos=prueba.puntos, run=prueba.run,
                salida=normalizar(ej.salida), error=normalizar(ej.error))
    if ej.agotado:
        return ResultadoPrueba(superada=False, puntos=0,
                               motivo=f"tardó más de {prueba.timeout} s", **base)
    total = superados = 0
    if informe.is_file():
        try:
            raiz = ET.parse(informe).getroot()
            for suite in ([raiz] if raiz.tag == "testsuite" else raiz.iter("testsuite")):
                n = int(suite.get("tests", 0))
                malos = sum(int(suite.get(k, 0)) for k in ("failures", "errors", "skipped"))
                total += n
                superados += n - malos
        except (ET.ParseError, ValueError):
            total = 0
        informe.unlink(missing_ok=True)
    if total:
        superada = superados == total
        puntos = max(0, min(prueba.puntos, round(prueba.puntos * superados / total)))
        if not superada:
            puntos = min(puntos, max(0, prueba.puntos - 1))
        return ResultadoPrueba(superada=superada, puntos=puntos,
                               motivo=f"pytest: {superados}/{total} casos superados", **base)
    superada = ej.codigo == 0
    return ResultadoPrueba(superada=superada, puntos=prueba.puntos if superada else 0,
                           motivo=f"pytest terminó con código {ej.codigo} (sin informe)", **base)


def evaluar_prueba(prueba: Prueba, ejecutar: Ejecutor, directorio: Path) -> ResultadoPrueba:
    base = dict(nombre=prueba.nombre, tipo=prueba.tipo, max_puntos=prueba.puntos, run=prueba.run)
    if prueba.setup:
        ej = ejecutar(prueba.setup, None, prueba.timeout)
        if ej.agotado or ej.codigo != 0:
            motivo = (f"la preparación tardó más de {prueba.timeout} s" if ej.agotado
                      else f"la preparación («{prueba.setup}») terminó con código {ej.codigo}")
            return ResultadoPrueba(superada=False, puntos=0, motivo=motivo,
                                   salida=normalizar(ej.salida), error=normalizar(ej.error), **base)
    if prueba.tipo == "python":
        return _evaluar_python(prueba, ejecutar, directorio)

    ej = ejecutar(prueba.run, prueba.entrada if prueba.tipo == "io" else None, prueba.timeout)
    salida, error = normalizar(ej.salida), normalizar(ej.error)
    if ej.agotado:
        return ResultadoPrueba(superada=False, puntos=0, entrada=prueba.entrada, salida=salida,
                               error=error, esperado=prueba.esperado,
                               comparacion=prueba.comparacion,
                               motivo=(f"tardó más de {prueba.timeout} s: ¿espera más datos de los "
                                       "que se le dan, o tiene un bucle que no termina?"), **base)
    if prueba.tipo == "run":
        superada = ej.codigo == prueba.codigo_salida
        return ResultadoPrueba(superada=superada, puntos=prueba.puntos if superada else 0,
                               salida=salida, error=error,
                               motivo=f"código de salida {ej.codigo} (se esperaba "
                                      f"{prueba.codigo_salida})", **base)
    try:
        superada = comparar(salida, prueba.esperado, prueba.comparacion)
    except re.error as exc:
        superada, motivo = False, f"expresión regular no válida en la prueba: {exc}"
    else:
        if superada:
            motivo = ""
        elif ej.codigo not in (0, None) and error.strip():
            ultima = error.strip().splitlines()[-1]
            motivo = f"el programa terminó con un error: {ultima}"
        else:
            motivo = "la salida no contiene lo esperado"
    return ResultadoPrueba(superada=superada, puntos=prueba.puntos if superada else 0,
                           motivo=motivo, entrada=prueba.entrada, esperado=prueba.esperado,
                           salida=salida, error=error, comparacion=prueba.comparacion, **base)


def evaluar(pruebas: list, ejecutar: Ejecutor, directorio: Path,
            al_terminar: Optional[Callable[[ResultadoPrueba], None]] = None) -> Resultado:
    resultado = Resultado()
    for prueba in pruebas:
        r = evaluar_prueba(prueba, ejecutar, Path(directorio))
        resultado.pruebas.append(r)
        if al_terminar:
            al_terminar(r)
    return resultado


def ejecutor_local(directorio: Path, python: Optional[str] = None) -> Ejecutor:
    """Ejecuta las órdenes con un subproceso en `directorio`. NO aísla nada: solo para el alumno
    en su propio ordenador o para pruebas del corrector. En el servidor, usar el contenedor.

    Con `python`, una orden que empieza por `python` o `python3` usa ese intérprete (en Windows
    `python` a veces es el acceso directo a la Microsoft Store)."""
    entorno = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                   PYTHONDONTWRITEBYTECODE="1")

    def ejecutar(orden: str, entrada: Optional[str], timeout: int) -> Ejecucion:
        if python:
            orden = re.sub(r"^python3?(?=\s|$)", lambda _: shlex.quote(python)
                           if os.name != "nt" else f'"{python}"', orden.strip())
        try:
            p = subprocess.run(orden, shell=True, cwd=directorio, input=entrada or "",
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, env=entorno)
        except subprocess.TimeoutExpired as exc:
            def _txt(x: object) -> str:
                return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
            return Ejecucion(None, _txt(exc.stdout), _txt(exc.stderr), agotado=True)
        return Ejecucion(p.returncode, p.stdout, p.stderr)

    return ejecutar
