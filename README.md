# Corrector de prácticas para Classroom 50

Corrige las prácticas de Python en servidores propios y deja la nota en Classroom 50 sin usar
GitHub Actions.

GitHub solo guarda los repositorios. Classroom 50 sigue siendo la interfaz: inscripción,
aceptación de la tarea, página de entregas y exportación de notas. La ejecución del código de los
alumnos ocurre aquí.

```
alumno ── push ──▶ repo del alumno (sin workflows: no gasta minutos)
                          │
              grader ◀────┘  (descarga el commit por la API)
               │
               ├─ ejecuta las pruebas de la plantilla en un contenedor sin red
               ├─▶ Release «Grading: 7/9» en el repo del alumno (lo que ve el alumno)
               └─▶ nota en classroom50/<clase>/scores.json (lo que ve Classroom 50)
```

El paquete y la orden se llaman `grader`. El código, los comentarios y los mensajes que ve el
alumno (el Release y `check.py`) están en inglés.

## Cómo encaja con Classroom 50

La tarea se crea en Classroom 50 con dos opciones:

- **Grading: Manual**, con los puntos de la práctica.
- **Do not use the built-in autograder** (`no_autograder`). Con ella el repositorio del alumno es
  una copia exacta de la plantilla, sin workflows, y los push no lanzan nada en Actions.

La nota que escribe el corrector es la misma que escribe la web cuando un profesor pone una nota a
mano: una entrada con `"override": true` en `scores.json`, copiada del código de la web
(`web/src/domain/assignments/scoreOverride.ts`). Por eso aparece en la página de entregas y en el
CSV de «Download scores», y el recolector de notas de Classroom 50 no la toca.

Dos detalles que importan:

- **`[skip ci]` en cada commit de notas.** Cada commit en el repositorio `classroom50` lanza su
  workflow `publish-pages`, que sí gasta minutos. El corrector agrupa las notas en un commit por
  clase y lleva `[skip ci]` en el mensaje. Cuidado: poner notas a mano en la web sí lanza ese
  workflow en cada nota.
- **Notas manuales del profesor.** Si un profesor pone una nota a mano en la web, el corrector no
  la pisa al volver a corregir; avisa en el registro. Con `--force` sí la pisa.

Las notas del corrector se reconocen por el prefijo `submit/manual-grader-`. Las que escribieron
versiones anteriores (`submit/manual-corrector-`) también se tratan como suyas, no como notas del
profesor.

Comprobado contra el código de Classroom 50 del 5 de noviembre de 2026 (commit `b6d81a4`). Si
Classroom 50 cambia el formato de `scores.json`, hay que revisar `publish.py`.

## Instalación en el servidor

Hace falta Linux con Docker y Python 3.11 o posterior. El corrector solo usa la biblioteca
estándar.

```bash
sudo useradd --system --home /var/lib/grader --groups docker grader
sudo python3 -m venv /opt/grader/venv
sudo /opt/grader/venv/bin/pip install /ruta/a/auto-corrector
docker build -t grader-sandbox:3.12 sandbox/      # imagen donde corre el código de alumnos

sudo mkdir -p /etc/grader
sudo cp grader.example.toml /etc/grader/grader.toml   # y ajustarlo
sudo install -m 600 -o grader /dev/null /etc/grader/secrets.env
```

Contenido de `/etc/grader/secrets.env`:

```
GITHUB_TOKEN=github_pat_...
GRADER_API_TOKEN=una-cadena-larga-y-aleatoria
```

### El token de GitHub

Un token *fine-grained* con estas características:

- **Propietario (resource owner):** la organización.
- **Repositorios:** todos (los de alumno se crean sobre la marcha).
- **Permisos:**
  - **Contents: Read and write**, para descargar código, crear Releases y escribir `scores.json`.
  - **Metadata: Read**.

El token caduca. Anota la fecha y renuévalo antes del cuatrimestre.

## Uso

### Por lotes: corregir y salir

```bash
grader grade -c grader.toml grupo-test/lab-02-prueba ana luis
grader grade -c grader.toml grupo-test/lab-02-prueba --all
grader grade -c grader.toml grupo-test/lab-02-prueba --all --until 2026-11-15T22:59:00Z
grader grade -c grader.toml grupo-test/lab-02-prueba ana --no-publish   # solo mirar
```

- **`--until`** corrige el último commit anterior a esa fecha: lo entregado en plazo.
- **Correcciones repetidas:** si un commit ya está corregido con las mismas pruebas, no se repite,
  salvo con `--force`. Si cambias las pruebas de la plantilla, las entregas se vuelven a corregir.

Para el cierre de cada laboratorio hay un ejemplo de cron en `deploy/grader-deadline.example`.

### Como servicio

```bash
grader serve -c grader.toml        # o con systemd: deploy/grader.service
```

Con `watch` en la configuración, cada `watch_every` segundos el servicio mira qué repositorios
de esas tareas han recibido un push y los corrige. Es una sola consulta a la API por vuelta y no
hace falta que GitHub pueda llegar al servidor.

La API lleva el token de `GRADER_API_TOKEN` en la cabecera `Authorization: Bearer …`:

| Petición | Qué hace |
|---|---|
| `GET /health` | Estado: trabajos en cola y notas pendientes. No pide token. |
| `POST /grade` | Cuerpo `{"classroom", "assignment", "students"?, "force"?, "until"?}`. Sin `students`, corrige a todos. Responde con los identificadores de los trabajos. |
| `GET /jobs` | Los últimos 100 trabajos. |
| `GET /jobs/<id>` | Estado y resumen de un trabajo. |
| `POST /watch` | Una vuelta de vigilancia ahora mismo. |

```bash
curl -H "Authorization: Bearer $GRADER_API_TOKEN" -d '{"classroom":"grupo-test","assignment":"lab-02-prueba","students":["ana"]}' http://127.0.0.1:8080/grade
```

### Herramientas para preparar prácticas

```bash
grader local ../prog-lab-2 ~/entrega-de-ana                        # corrige una carpeta, sin GitHub
grader validate-assignment ../prog-lab-2 --solution ~/soluciones/lab-02
grader sync-engine ../prog-lab-2 ../prog-lab-3                     # tras cambiar engine.py
```

`validate-assignment` comprueba lo siguiente:

- las pruebas cargan;
- no hay workflows en la plantilla;
- el motor de la práctica (`.grader/engine.py`) es el actual;
- la plantilla sin tocar saca 0;
- con la solución de referencia se saca la nota máxima.

Pásalo siempre antes de publicar una práctica.

## Las pruebas

Cada práctica lleva una carpeta `.grader/` con:

- `assignment.toml`: tabla `[assignment]` con `tests` (el fichero de pruebas, `tests.json` por
  defecto) y `protected` (patrones de ficheros que se toman siempre de la plantilla);
- `tests.json`: las pruebas;
- `engine.py`: el motor, que usa `check.py` en el ordenador del alumno.

`tests.json` usa el formato de pruebas de Classroom 50: el mismo array de
`gh teacher assignment add --tests`. Los ficheros de pruebas que ya tenéis generados para los
laboratorios, las prácticas y los exámenes valen tal cual; solo hay que llamarlos `tests.json`.

`engine.py` reproduce la semántica del corrector de Classroom 50 (`runner.py`):

- **`io`:** `included` es una subcadena literal, `exact` compara ignorando los espacios del
  principio y del final, y `regex` usa `re.search` multilínea.
- **`run`:** pasa según el código de salida.
- **`python`:** pytest reparte los puntos por casos superados, con el informe JUnit que trae
  pytest, sin plugins.

Una mejora sobre Classroom 50: cuando una prueba falla, el informe señala la primera línea distinta
y la columna, con los espacios marcados con `·`.

`extra_tests` en la configuración añade pruebas que el alumno no ve. Se guardan en el servidor,
no en la plantilla.

## Seguridad

- **Aislamiento.** Cada entrega se ejecuta en un contenedor nuevo con estas condiciones:
  - sin red;
  - sin capacidades ni escalada de privilegios;
  - sistema de ficheros de solo lectura salvo la carpeta de la entrega y un `/tmp` de 64 MB;
  - límites de memoria, CPU y número de procesos;
  - `timeout` en cada orden.

  El contenedor se destruye al terminar.
- **Descarga segura.** Los tarballs se descomprimen con el filtro `data` de `tarfile` y sin
  enlaces: una entrega no puede escribir fuera de su carpeta.
- **Pruebas intocables.** La carpeta `.grader/` y los ficheros `protected` de la práctica se
  toman siempre de la plantilla. Lo que el alumno cambie ahí no cuenta.
- **El token nunca entra en el contenedor.**

## Limitaciones conocidas

- **Solo tareas individuales.** Las de grupo dan error.
- **La fecha de entrega es la del commit.** El alumno puede falsearla. Para un cierre estricto,
  corrige con `--until` justo al cerrar.
- **El Release se puede borrar.** Lo crea el corrector, pero el alumno puede borrarlo en su
  repositorio. La nota que cuenta es la de `scores.json`.
- **Fallos en la vigilancia.** Si una corrección automática falla (por ejemplo, GitHub no
  responde), no se reintenta hasta el siguiente push. Mira `GET /jobs` o el registro, y relanza
  con `POST /grade`.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest                                                     # 18 pruebas, sin red ni Docker
CLASSROOM50_SCHEMAS=~/classroom50/schemas pytest           # valida además contra los esquemas
```

Las pruebas usan un GitHub falso (`tests/fake_github.py`) y una copia de la plantilla del
laboratorio 2 en `tests/fixtures/lab_02/template/`. Reproducen el piloto del 13 de octubre:

| Entrega | Nota |
|---|---|
| Plantilla sin tocar | 0/9 |
| Ejercicio 1 resuelto | 3/9 |
| Ejercicio 1 con un espacio de más | 0/9 |
| Todo resuelto | 9/9 |
