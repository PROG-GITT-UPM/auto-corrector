import json
import shutil
import sys
from pathlib import Path

import pytest

from corrector import motor

FIXTURES = Path(__file__).parent / "fixtures" / "lab_02"
PRACTICA = Path(__file__).resolve().parents[2] / "prog-lab-02"


def corregir(carpeta: Path, pruebas=None) -> motor.Resultado:
    pruebas = pruebas or motor.cargar_pruebas(FIXTURES / "pruebas.json")
    return motor.evaluar(pruebas, motor.ejecutor_local(carpeta, sys.executable), carpeta)


@pytest.fixture
def entrega(tmp_path: Path) -> Path:
    for f in PRACTICA.glob("ejercicio_*.py"):
        shutil.copy(f, tmp_path)
    return tmp_path


def test_plantilla_sin_tocar_saca_0(entrega):
    r = corregir(entrega)
    assert (r.puntos, r.max_puntos) == (0, 9)


def test_ejercicio_1_resuelto_saca_3(entrega):
    shutil.copy(FIXTURES / "solucion" / "ejercicio_01_telefono.py", entrega)
    assert corregir(entrega).puntos == 3


def test_espacio_de_mas_falla_y_se_senala(entrega):
    texto = (FIXTURES / "solucion" / "ejercicio_01_telefono.py").read_text(encoding="utf-8")
    texto = texto.replace('"Es fijo:              ', '"Es fijo:               ')
    (entrega / "ejercicio_01_telefono.py").write_text(texto, encoding="utf-8")
    r = corregir(entrega)
    assert r.puntos == 0
    explicacion = motor.explicar_diferencia(r.pruebas[0].esperado, r.pruebas[0].salida)
    assert "Es·fijo:···············False" in explicacion
    assert "columna 23" in explicacion


def test_solucion_completa_saca_9(entrega):
    shutil.copytree(FIXTURES / "solucion", entrega, dirs_exist_ok=True)
    assert corregir(entrega).puntos == 9


def _pruebas(tmp_path: Path, specs: list) -> list:
    ruta = tmp_path / "p.json"
    ruta.write_text(json.dumps(specs), encoding="utf-8")
    return motor.cargar_pruebas(ruta)


def test_exact_regex_run_y_setup(tmp_path):
    (tmp_path / "hola.py").write_text("print('  Hola, ' + input() + '  ')\n", encoding="utf-8")
    pruebas = _pruebas(tmp_path, [
        {"name": "exact", "type": "io", "run": "python hola.py", "input": "Ana\n",
         "expected": "Hola, Ana", "comparison": "exact", "points": 1},
        {"name": "regex", "type": "io", "run": "python hola.py", "input": "Eva\n",
         "expected": r"^\s*Hola,\s+Eva\b", "comparison": "regex", "points": 2},
        {"name": "run", "type": "run", "run": "python -c 'import sys; sys.exit(3)'",
         "exit-code": 3, "points": 1},
        {"name": "setup falla", "type": "io", "setup": "python -c 'raise SystemExit(1)'",
         "run": "python hola.py", "input": "x\n", "expected": "Hola", "comparison": "included",
         "points": 1},
    ])
    r = corregir(tmp_path, pruebas)
    assert [p.superada for p in r.pruebas] == [True, True, True, False]
    assert "preparación" in r.pruebas[3].motivo
    assert (r.puntos, r.max_puntos) == (4, 5)


def test_programa_que_espera_mas_datos_agota_el_tiempo(tmp_path):
    (tmp_path / "bucle.py").write_text("while True:\n    input()\n", encoding="utf-8")
    pruebas = _pruebas(tmp_path, [{"name": "t", "type": "io", "run": "python bucle.py",
                                   "input": "1\n", "expected": "x", "comparison": "included",
                                   "timeout": 1, "points": 1}])
    r = corregir(tmp_path, pruebas)
    assert not r.pruebas[0].superada
    # Con la entrada agotada input() lanza EOFError: el motivo cuenta el error, no un timeout.
    assert "error" in r.pruebas[0].motivo or "tardó" in r.pruebas[0].motivo


def test_bucle_infinito_agota_el_tiempo(tmp_path):
    (tmp_path / "bucle.py").write_text("while True:\n    pass\n", encoding="utf-8")
    pruebas = _pruebas(tmp_path, [{"name": "t", "type": "run", "run": "python bucle.py",
                                   "timeout": 1, "points": 1}])
    r = corregir(tmp_path, pruebas)
    assert "tardó más de 1 s" in r.pruebas[0].motivo


def test_python_reparte_puntos(tmp_path):
    pytest.importorskip("pytest")
    (tmp_path / "test_x.py").write_text(
        "def test_a(): assert True\ndef test_b(): assert True\ndef test_c(): assert False\n"
        "def test_d(): assert True\n", encoding="utf-8")
    pruebas = _pruebas(tmp_path, [{"name": "pytest", "type": "python",
                                   "run": f"{sys.executable} -m pytest -q", "points": 8}])
    r = corregir(tmp_path, pruebas)
    assert r.pruebas[0].puntos == 6 and not r.pruebas[0].superada
    assert "3/4" in r.pruebas[0].motivo


def test_pruebas_no_validas(tmp_path):
    with pytest.raises(motor.ErrorPruebas):
        _pruebas(tmp_path, [{"name": "a", "type": "io", "run": "x", "comparison": "parecido"}])
    with pytest.raises(motor.ErrorPruebas):
        _pruebas(tmp_path, [{"name": "a", "type": "run", "run": "x"},
                            {"name": "a", "type": "run", "run": "y"}])
    with pytest.raises(motor.ErrorPruebas):
        _pruebas(tmp_path, [{"name": "a", "type": "io", "run": "x", "comparison": "exact",
                             "expected-file": "../../etc/passwd"}])
