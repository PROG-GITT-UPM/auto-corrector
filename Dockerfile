# The grader service: FastAPI app + background workers.
#   docker compose build      (builds this image and the sandbox image)
FROM docker:27-cli AS docker-cli

FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/PROG-GITT-UPM/auto-grader

# Only the docker CLI: the daemon is the host's, reached through the socket proxy (DOCKER_HOST).
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

# uid 10001 is also the sandbox user, so files copied into a sandbox keep an owner the student
# code can write as.
RUN useradd --create-home --uid 10001 grader \
 && install -d -o 10001 -g 10001 /var/lib/grader

WORKDIR /usr/src/fastapi
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ./code/main.py .
COPY ./code/src ./src

USER grader
EXPOSE 8000
# One process only: the background workers live in it. Grading parallelism is GRADER_WORKERS.
CMD ["fastapi", "run", "main.py", "--port", "8000", "--workers", "1"]
