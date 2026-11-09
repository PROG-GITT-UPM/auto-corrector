"""Un GitHub de mentira con las llamadas que usa el corrector, para probar sin red.

Guarda los repositorios en memoria ({nombre: {rama: [(sha, fecha, {ruta: bytes})]}}) y registra
cada commit, Release y escritura para que las pruebas puedan comprobar qué se hizo.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class GitHubFalso:
    def __init__(self, org: str) -> None:
        self.org = org
        self.repos: dict = {}        # nombre -> {"commits": [...], "pushed_at": str}
        self.releases: dict = {}     # nombre -> [release]
        self.assets: dict = {}       # release id -> {nombre: bytes}
        self.ficheros: dict = {}     # (repo, ruta) -> (bytes, sha)
        self.commits_contenido: list = []   # mensajes de commit en la API de contenidos
        self.peticiones: list = []
        self._id = 0
        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), self._manejador())
        self.url = f"http://127.0.0.1:{self.servidor.server_address[1]}"
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def cerrar(self) -> None:
        self.servidor.shutdown()

    # ─── Preparar datos ──────────────────────────────────────────────────────

    def commit(self, repo: str, ficheros: dict, fecha: str) -> str:
        r = self.repos.setdefault(repo, {"commits": [], "pushed_at": fecha})
        sha = hashlib.sha1(f"{repo}{fecha}{sorted(ficheros.items())}".encode()).hexdigest()
        r["commits"].insert(0, {"sha": sha, "fecha": fecha, "ficheros": dict(ficheros)})
        r["pushed_at"] = fecha
        return sha

    def poner_fichero(self, repo: str, ruta: str, contenido: str) -> None:
        datos = contenido.encode()
        self.ficheros[(repo, ruta)] = (datos, hashlib.sha1(datos).hexdigest())

    def leer(self, repo: str, ruta: str) -> str:
        return self.ficheros[(repo, ruta)][0].decode()

    def _nuevo_id(self) -> int:
        self._id += 1
        return self._id

    def _tarball(self, repo: str, ref: str) -> bytes:
        commits = self.repos[repo]["commits"]
        c = next(c for c in commits if c["sha"] == ref or ref in ("main", "HEAD"))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            raiz = f"{self.org}-{repo}-{c['sha'][:7]}/"
            for ruta, datos in c["ficheros"].items():
                info = tarfile.TarInfo(raiz + ruta)
                info.size = len(datos)
                tar.addfile(info, io.BytesIO(datos))
        return buf.getvalue()

    # ─── Servidor ────────────────────────────────────────────────────────────

    def _manejador(self):
        gh = self

        class M(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, codigo, datos, cabeceras=None):
                cuerpo = json.dumps(datos).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                for k, v in (cabeceras or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

            def _cuerpo(self):
                n = int(self.headers.get("Content-Length", 0))
                return self.rfile.read(n)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                gh.peticiones.append(("GET", u.path))
                org = gh.org
                if u.path == f"/orgs/{org}/repos":
                    lista = sorted(gh.repos.items(), key=lambda kv: kv[1]["pushed_at"], reverse=True)
                    por_pagina, pagina = int(q.get("per_page", 30)), int(q.get("page", 1))
                    trozo = lista[(pagina - 1) * por_pagina: pagina * por_pagina]
                    cab = {}
                    if pagina * por_pagina < len(lista):
                        cab["Link"] = (f'<{gh.url}{u.path}?per_page={por_pagina}&page={pagina + 1}>;'
                                       ' rel="next"')
                    return self._json(200, [{"name": n, "pushed_at": r["pushed_at"]}
                                            for n, r in trozo], cab)
                m = re.fullmatch(rf"/repos/{org}/([^/]+)(/.*)?", u.path)
                if not m:
                    return self._json(404, {"message": "Not Found"})
                repo, resto = m.group(1), m.group(2) or ""
                if resto.startswith("/contents/"):
                    ruta = resto[len("/contents/"):]
                    if (repo, ruta) not in gh.ficheros:
                        return self._json(404, {"message": "Not Found"})
                    datos, sha = gh.ficheros[(repo, ruta)]
                    return self._json(200, {"encoding": "base64", "sha": sha,
                                            "content": base64.b64encode(datos).decode()})
                if repo not in gh.repos:
                    return self._json(404, {"message": "Not Found"})
                commits = gh.repos[repo]["commits"]
                if resto == "":
                    return self._json(200, {"name": repo, "default_branch": "main"})
                if resto == "/commits":
                    lista = [c for c in commits if not q.get("until") or c["fecha"] <= q["until"]]
                    return self._json(200, [{"sha": c["sha"], "commit": {"committer": {
                        "date": c["fecha"]}}} for c in lista[:int(q.get("per_page", 30))]])
                if resto.startswith("/commits/"):
                    return self._json(200, {"sha": commits[0]["sha"]})
                if resto.startswith("/tarball/"):
                    datos = gh._tarball(repo, resto.split("/", 2)[2])
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(datos)))
                    self.end_headers()
                    return self.wfile.write(datos)
                if resto == "/releases":
                    return self._json(200, list(reversed(gh.releases.get(repo, []))))
                return self._json(404, {"message": "Not Found"})

            def do_PUT(self):
                u = urlparse(self.path)
                gh.peticiones.append(("PUT", u.path))
                m = re.fullmatch(rf"/repos/{gh.org}/([^/]+)/contents/(.+)", u.path)
                datos = json.loads(self._cuerpo())
                repo, ruta = m.group(1), m.group(2)
                actual = gh.ficheros.get((repo, ruta))
                if actual and datos.get("sha") != actual[1]:
                    return self._json(409, {"message": "does not match"})
                gh.poner_fichero(repo, ruta, base64.b64decode(datos["content"]).decode())
                gh.commits_contenido.append(datos["message"])
                return self._json(200, {"commit": {"sha": "x"}})

            def do_POST(self):
                u = urlparse(self.path)
                gh.peticiones.append(("POST", u.path))
                m = re.fullmatch(rf"/repos/{gh.org}/([^/]+)/releases", u.path)
                if m:
                    datos = json.loads(self._cuerpo())
                    ident = gh._nuevo_id()
                    rel = {"id": ident, "tag_name": datos["tag_name"], "name": datos["name"],
                           "body": datos["body"], "target_commitish": datos["target_commitish"],
                           "html_url": f"https://github.com/{gh.org}/{m.group(1)}/releases/tag/"
                                       f"{datos['tag_name']}",
                           "upload_url": f"{gh.url}/uploads/{ident}{{?name,label}}"}
                    gh.releases.setdefault(m.group(1), []).append(rel)
                    return self._json(201, rel)
                m = re.fullmatch(r"/uploads/(\d+)", u.path)
                if m:
                    nombre = parse_qs(u.query)["name"][0]
                    gh.assets.setdefault(int(m.group(1)), {})[nombre] = self._cuerpo()
                    return self._json(201, {"name": nombre})
                return self._json(404, {"message": "Not Found"})

        return M
