"""A fake GitHub with the calls the grader uses, for testing without network.

It keeps repositories in memory ({name: {"commits": [{sha, date, files: {path: bytes}}], ...}})
and records every commit, Release and write so that the tests can check what was done.
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


class FakeGitHub:
    def __init__(self, org: str) -> None:
        self.org = org
        self.repos: dict = {}        # name -> {"commits": [...], "pushed_at": str}
        self.releases: dict = {}     # name -> [release]
        self.assets: dict = {}       # release id -> {name: bytes}
        self.files: dict = {}        # (repo, path) -> (bytes, sha)
        self.content_commits: list = []   # commit messages in the contents API
        self.requests: list = []
        self._id = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()

    # ─── Setting up data ─────────────────────────────────────────────────────

    def commit(self, repo: str, files: dict, date: str) -> str:
        r = self.repos.setdefault(repo, {"commits": [], "pushed_at": date})
        sha = hashlib.sha1(f"{repo}{date}{sorted(files.items())}".encode()).hexdigest()
        r["commits"].insert(0, {"sha": sha, "date": date, "files": dict(files)})
        r["pushed_at"] = date
        return sha

    def put_file(self, repo: str, path: str, content: str) -> None:
        data = content.encode()
        self.files[(repo, path)] = (data, hashlib.sha1(data).hexdigest())

    def read(self, repo: str, path: str) -> str:
        return self.files[(repo, path)][0].decode()

    def _new_id(self) -> int:
        self._id += 1
        return self._id

    def _tarball(self, repo: str, ref: str) -> bytes:
        commits = self.repos[repo]["commits"]
        c = next(c for c in commits if c["sha"] == ref or ref in ("main", "HEAD"))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            root = f"{self.org}-{repo}-{c['sha'][:7]}/"
            for path, data in c["files"].items():
                info = tarfile.TarInfo(root + path)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    # ─── Server ──────────────────────────────────────────────────────────────

    def _handler(self):
        gh = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, data, headers=None):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length", 0))
                return self.rfile.read(n)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                gh.requests.append(("GET", u.path))
                org = gh.org
                if u.path == f"/orgs/{org}/repos":
                    repos = sorted(gh.repos.items(), key=lambda kv: kv[1]["pushed_at"], reverse=True)
                    per_page, page = int(q.get("per_page", 30)), int(q.get("page", 1))
                    chunk = repos[(page - 1) * per_page: page * per_page]
                    headers = {}
                    if page * per_page < len(repos):
                        headers["Link"] = (f'<{gh.url}{u.path}?per_page={per_page}&page={page + 1}>;'
                                           ' rel="next"')
                    return self._json(200, [{"name": n, "pushed_at": r["pushed_at"]}
                                            for n, r in chunk], headers)
                m = re.fullmatch(rf"/repos/{org}/([^/]+)(/.*)?", u.path)
                if not m:
                    return self._json(404, {"message": "Not Found"})
                repo, rest = m.group(1), m.group(2) or ""
                if rest.startswith("/contents/"):
                    path = rest[len("/contents/"):]
                    if (repo, path) not in gh.files:
                        return self._json(404, {"message": "Not Found"})
                    data, sha = gh.files[(repo, path)]
                    return self._json(200, {"encoding": "base64", "sha": sha,
                                            "content": base64.b64encode(data).decode()})
                if repo not in gh.repos:
                    return self._json(404, {"message": "Not Found"})
                commits = gh.repos[repo]["commits"]
                if rest == "":
                    return self._json(200, {"name": repo, "default_branch": "main"})
                if rest == "/commits":
                    selected = [c for c in commits if not q.get("until") or c["date"] <= q["until"]]
                    return self._json(200, [{"sha": c["sha"], "commit": {"committer": {
                        "date": c["date"]}}} for c in selected[:int(q.get("per_page", 30))]])
                if rest.startswith("/commits/"):
                    return self._json(200, {"sha": commits[0]["sha"]})
                if rest.startswith("/tarball/"):
                    data = gh._tarball(repo, rest.split("/", 2)[2])
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
                if rest == "/releases":
                    return self._json(200, list(reversed(gh.releases.get(repo, []))))
                return self._json(404, {"message": "Not Found"})

            def do_PUT(self):
                u = urlparse(self.path)
                gh.requests.append(("PUT", u.path))
                m = re.fullmatch(rf"/repos/{gh.org}/([^/]+)/contents/(.+)", u.path)
                data = json.loads(self._body())
                repo, path = m.group(1), m.group(2)
                current = gh.files.get((repo, path))
                if current and data.get("sha") != current[1]:
                    return self._json(409, {"message": "does not match"})
                gh.put_file(repo, path, base64.b64decode(data["content"]).decode())
                gh.content_commits.append(data["message"])
                return self._json(200, {"commit": {"sha": "x"}})

            def do_POST(self):
                u = urlparse(self.path)
                gh.requests.append(("POST", u.path))
                m = re.fullmatch(rf"/repos/{gh.org}/([^/]+)/releases", u.path)
                if m:
                    data = json.loads(self._body())
                    ident = gh._new_id()
                    rel = {"id": ident, "tag_name": data["tag_name"], "name": data["name"],
                           "body": data["body"], "target_commitish": data["target_commitish"],
                           "html_url": f"https://github.com/{gh.org}/{m.group(1)}/releases/tag/"
                                       f"{data['tag_name']}",
                           "upload_url": f"{gh.url}/uploads/{ident}{{?name,label}}"}
                    gh.releases.setdefault(m.group(1), []).append(rel)
                    return self._json(201, rel)
                m = re.fullmatch(r"/uploads/(\d+)", u.path)
                if m:
                    name = parse_qs(u.query)["name"][0]
                    gh.assets.setdefault(int(m.group(1)), {})[name] = self._body()
                    return self._json(201, {"name": name})
                return self._json(404, {"message": "Not Found"})

        return H
