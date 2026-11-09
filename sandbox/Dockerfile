# Imagen en la que se ejecuta el código de los alumnos.
#   docker build -t corrector-sandbox:3.12 sandbox/
# Solo lleva Python y pytest. El contenedor se lanza sin red (--network none), así que todo lo que
# necesiten las prácticas (bibliotecas, datos) tiene que estar aquí o en el repositorio.
FROM python:3.12-slim

RUN pip install --no-cache-dir "pytest>=8,<9" \
 && useradd --create-home --uid 10001 alumno

# Si una práctica necesita bibliotecas, añádelas aquí, con versión fija:
# RUN pip install --no-cache-dir "numpy==2.1.*"

USER alumno
WORKDIR /entrega
CMD ["sleep", "infinity"]
