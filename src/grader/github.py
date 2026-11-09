"""Minimal client for the GitHub REST API, without dependencies.

No call in this module triggers GitHub Actions: reading repositories, downloading code, creating
Releases and writing files with the contents API.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str, url: str = "") -> None:
        super().__init__(f"GitHub {status} at {url}: {message}")
        self.status = status
        self.message = message


class Conflict(GitHubError):
    """The file changed between reading and writing (another commit got there first)."""


class GitHub:
    def __init__(self, token: str, api: str = "https://api.github.com",
                 web: str = "https://github.com", retries: int = 4) -> None:
        if not token:
            raise ValueError("the GitHub token is missing")
        self.token = token
        self.api = api.rstrip("/")
        self.web = web.rstrip("/")
        self.retries = retries

    # ─── Requests ────────────────────────────────────────────────────────────

    def _request(self, method: str, url: str, body: Optional[bytes] = None,
                 content_type: str = "application/json",
                 accept: str = "application/vnd.github+json") -> tuple[int, dict, bytes]:
        if not url.startswith("http"):
            url = f"{self.api}{url}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "grader-prog",
        }
        if body is not None:
            headers["Content-Type"] = content_type
        wait = 2.0
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.status, dict(resp.headers), resp.read()
            except urllib.error.HTTPError as exc:
                data = exc.read()
                status = exc.code
                remaining = exc.headers.get("x-ratelimit-remaining")
                if status in (403, 429) and (remaining == "0" or exc.headers.get("retry-after")):
                    retry_after = exc.headers.get("retry-after")
                    pause = float(retry_after) if retry_after else max(
                        1.0, float(exc.headers.get("x-ratelimit-reset", time.time() + 60)) - time.time())
                    log.warning("GitHub API rate limit; waiting %.0f s", pause)
                    time.sleep(min(pause, 900))
                    continue
                if status >= 500 and attempt < self.retries:
                    time.sleep(wait)
                    wait *= 2
                    continue
                try:
                    message = json.loads(data).get("message", "")
                except (json.JSONDecodeError, AttributeError):
                    message = data[:200].decode("utf-8", "replace")
                error_class = Conflict if status == 409 or (
                    status == 422 and "sha" in message.lower()) else GitHubError
                raise error_class(status, message, url) from None
            except urllib.error.URLError as exc:
                if attempt < self.retries:
                    time.sleep(wait)
                    wait *= 2
                    continue
                raise GitHubError(0, str(exc.reason), url) from None
        raise GitHubError(0, "too many retries", url)

    def get(self, path: str, **params: Any) -> Any:
        if params:
            path = f"{path}?{urllib.parse.urlencode(params)}"
        _, _, data = self._request("GET", path)
        return json.loads(data) if data else None

    def send(self, method: str, path: str, data: Any) -> Any:
        _, _, resp = self._request(method, path, json.dumps(data).encode("utf-8"))
        return json.loads(resp) if resp else None

    def paginate(self, path: str, **params: Any) -> Iterator[Any]:
        params.setdefault("per_page", 100)
        url: Optional[str] = f"{path}?{urllib.parse.urlencode(params)}"
        while url:
            _, headers, data = self._request("GET", url)
            yield from json.loads(data)
            links = headers.get("Link") or headers.get("link") or ""
            following = re.search(r'<([^>]+)>;\s*rel="next"', links)
            url = following.group(1) if following else None

    # ─── Operations ──────────────────────────────────────────────────────────

    def download_tarball(self, owner: str, repo: str, ref: str) -> bytes:
        _, _, data = self._request("GET", f"/repos/{owner}/{repo}/tarball/{ref}")
        return data

    def read_file(self, owner: str, repo: str, path: str,
                  ref: Optional[str] = None) -> tuple[str, str]:
        """(content, blob sha). Uses the blobs API if the file is larger than 1 MB."""
        params = {"ref": ref} if ref else {}
        info = self.get(f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}", **params)
        if info.get("encoding") == "base64" and info.get("content"):
            return base64.b64decode(info["content"]).decode("utf-8"), info["sha"]
        blob = self.get(f"/repos/{owner}/{repo}/git/blobs/{info['sha']}")
        return base64.b64decode(blob["content"]).decode("utf-8"), info["sha"]

    def write_file(self, owner: str, repo: str, path: str, content: str,
                   message: str, sha: Optional[str]) -> dict:
        """One commit with the file. Raises `Conflict` if `sha` is no longer the current one."""
        data: dict = {"message": message,
                      "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
        if sha:
            data["sha"] = sha
        return self.send("PUT", f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}",
                         data)

    def upload_asset(self, upload_url: str, name: str, data: bytes,
                     content_type: str = "application/json") -> dict:
        url = re.sub(r"\{.*\}$", "", upload_url) + "?" + urllib.parse.urlencode({"name": name})
        _, _, resp = self._request("POST", url, data, content_type=content_type)
        return json.loads(resp)
