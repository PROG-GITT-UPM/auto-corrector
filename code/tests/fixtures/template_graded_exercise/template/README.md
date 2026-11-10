# Práctica de prueba del corrector

Este es tu repositorio de la práctica de prueba del corrector. Tiene tres ejercicios cortos.

## Qué hay aquí

| Fichero | Ejercicio |
|---|---|
| `exercise_01_greeting.py` | Saludo |
| `exercise_02_average.py` | Media de los números de `data/numbers.txt` |
| `exercise_03_temperature.py` | Conversión de Celsius a Fahrenheit |
| `data/numbers.txt` | Los números del ejercicio 2. No lo cambies |
| `check.py` | Comprueba tus ejercicios antes de entregar |

Cada fichero de ejercicio empieza con un comentario que dice qué hay que hacer. Escribe tu
programa debajo. No cambies la carpeta `.grader/`.

## Antes de entregar: comprueba

En VS Code: **Terminal → Ejecutar tarea… → Comprobar mis ejercicios**. O en el terminal:

```bash
python check.py        # todos los ejercicios
python check.py 1      # solo el ejercicio 1
```

`check.py` ejecuta tus programas con las mismas pruebas que el corrector y compara tu salida con
la esperada. Si algo falla, te enseña la primera línea distinta, con los espacios marcados con `·`,
y la columna en la que difiere.

Las pruebas del ejercicio 3 usan pytest. Si `check.py` dice que no encuentra pytest, instálalo con
`python -m pip install pytest`.

## Cómo se entrega

Cada vez que haces `push`, tu trabajo queda entregado. Haz un commit cada vez que termines un
ejercicio:

```bash
git add exercise_01_greeting.py
git commit -m "Ejercicio 1 terminado"
git push
```

## Dónde ves tu corrección

El corrector de la asignatura pasa las mismas pruebas que `check.py` a lo que has subido. Unos
minutos después de tu `push`, en la página de tu repositorio, en **Releases**, aparece una entrada
**Grading** con el resultado de cada prueba.
