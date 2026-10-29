"""Makimono API クライアント。

API 仕様が未確定でも使えるように、認証方式・エンドポイント・ページネーションを
すべて設定可能にした汎用クライアント。
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests


@dataclass
class APIResponse:
    url: str
    status: int
    content_type: str
    elapsed_ms: int
    body: Any  # dict/list (JSON) または str
    is_json: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    def preview(self, n: int = 300) -> str:
        if self.error:
            return f"ERROR: {self.error}"
        if self.is_json:
            s = json.dumps(self.body, ensure_ascii=False)
        else:
            s = re.sub(r"\s+", " ", str(self.body))
        return s[:n] + ("…" if len(s) > n else "")


@dataclass
class MakimonoClient:
    base_url: str
    api_key: str = ""
    auth_mode: str = "header"  # header | bearer | query | none
    header_name: str = "X-API-Key"
    param_name: str = "api_key"
    timeout: float = 30.0
    verify_ssl: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/") + "/"
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                # WAF 等に弾かれにくいよう、一般的なブラウザ風の UA を既定にする
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 makimono-viz/0.1",
                "Accept-Language": "ja,en;q=0.8",
            }
        )
        self._session.headers.update(self.extra_headers)

    # ------------------------------------------------------------------ auth
    def _apply_auth(self, headers: dict, params: dict) -> None:
        if not self.api_key or self.auth_mode == "none":
            return
        if self.auth_mode == "header":
            headers[self.header_name] = self.api_key
        elif self.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.auth_mode == "query":
            params[self.param_name] = self.api_key
        else:
            raise ValueError(f"unknown auth_mode: {self.auth_mode}")

    def url_for(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.base_url, path.lstrip("/"))

    # --------------------------------------------------------------- request
    def request(
        self,
        path: str,
        method: str = "GET",
        params: dict | None = None,
        json_body: Any = None,
        data: dict | None = None,
    ) -> APIResponse:
        headers: dict[str, str] = {}
        params = dict(params or {})
        self._apply_auth(headers, params)
        url = self.url_for(path)
        t0 = time.perf_counter()
        try:
            resp = self._session.request(
                method.upper(),
                url,
                params=params,
                json=json_body,
                data=data,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:  # ネットワーク系
            return APIResponse(url, 0, "", int((time.perf_counter() - t0) * 1000), "", False, str(exc))
        elapsed = int((time.perf_counter() - t0) * 1000)
        ctype = resp.headers.get("Content-Type", "")
        body: Any
        is_json = False
        try:
            body = resp.json()
            is_json = True
        except ValueError:
            body = resp.text
        return APIResponse(resp.url, resp.status_code, ctype, elapsed, body, is_json)

    # ------------------------------------------------------------ pagination
    def fetch_all(
        self,
        path: str,
        method: str = "GET",
        params: dict | None = None,
        json_body: Any = None,
        page_param: str | None = "page",
        start_page: int = 1,
        max_pages: int = 100,
        records_path: str | None = None,
    ) -> tuple[list[Any], list[APIResponse]]:
        """ページネーションを辿りながら全件取得する。

        - page_param が None なら 1 回だけ取得。
        - レスポンス中に next / next_page / next_url があればそれを優先。
        - 取得件数が 0、または直前ページと同一ならば終了。
        """
        from .data_utils import extract_records, get_by_path

        responses: list[APIResponse] = []
        all_records: list[Any] = []
        params = dict(params or {})
        page = start_page
        last_sig: str | None = None
        next_url: str | None = None

        for _ in range(max_pages):
            if next_url:
                resp = self.request(next_url, method, params=None, json_body=json_body)
            else:
                p = dict(params)
                if page_param:
                    p[page_param] = page
                resp = self.request(path, method, params=p, json_body=json_body)
            responses.append(resp)
            if not resp.ok or not resp.is_json:
                break

            recs = get_by_path(resp.body, records_path) if records_path else None
            if recs is None:
                recs, used_path = extract_records(resp.body)
                # 1 ページ目で見つけたレコード配列のパスを以降のページでも使う
                # （配列が空になると別の場所を誤検出するのを防ぐ）
                if records_path is None and used_path and used_path != "$":
                    records_path = used_path
            if not isinstance(recs, list):
                recs = [recs] if isinstance(recs, dict) else []
            recs = [r for r in recs if isinstance(r, dict)]
            if not recs:
                break
            sig = json.dumps(recs[:3], sort_keys=True, ensure_ascii=False, default=str)
            if sig == last_sig:
                break
            last_sig = sig
            all_records.extend(recs)

            next_url = _find_next_url(resp.body, self.base_url)
            if not page_param and not next_url:
                break
            page += 1
        return all_records, responses

    # ------------------------------------------------------------------ probe
    def probe(self, paths: Iterable[str], methods: Iterable[str] = ("GET",)) -> list[APIResponse]:
        results: list[APIResponse] = []
        for p in paths:
            for m in methods:
                results.append(self.request(p, m))
        return results


def _find_next_url(body: Any, base_url: str) -> str | None:
    """レスポンス内の「次ページ」URL を探す（REST API でよくあるキー名）。"""
    if not isinstance(body, dict):
        return None
    for container in (body, body.get("meta"), body.get("pagination"), body.get("links"), body.get("paging")):
        if not isinstance(container, dict):
            continue
        for key in ("next", "next_url", "next_page_url", "nextPage", "next_page"):
            v = container.get(key)
            if isinstance(v, str) and v and ("/" in v or "?" in v):
                return v if urlparse(v).scheme else urljoin(base_url, v.lstrip("/"))
    return None


# 探索時に試す代表的なパス（PHP 製 ELN / 一般的な REST API を想定）
COMMON_ENDPOINTS: list[str] = [
    "api.php",
    "api.php?action=list",
    "api/",
    "api/v1/",
    "api/v2/",
    "api/experiments",
    "api/v1/experiments",
    "api/v2/experiments",
    "api/notes",
    "api/v1/notes",
    "api/entries",
    "api/v1/entries",
    "api/items",
    "api/v1/items",
    "api/records",
    "api/data",
    "api/projects",
    "api/v1/projects",
    "api/notebooks",
    "api/v1/notebooks",
    "api/samples",
    "api/users/me",
    "api/v1/users/me",
    "api/me",
    "api/status",
    "api/info",
    "experiments.php",
    "notes.php",
    "export.php",
    "export.php?format=json",
    "data.php",
    "list.php",
    "dashboard.php?format=json",
    "dashboard.php?output=json",
]


# ------------------------------------------------------------------ 診断
def diagnose(base_url: str, timeout: float = 10.0) -> list[dict]:
    """DNS → TCP → TLS → HTTP の順に接続を試し、どの段階で失敗するかを返す。"""
    from urllib.parse import urlparse

    u = urlparse(base_url if "://" in base_url else "https://" + base_url)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    steps: list[dict] = []

    def rec(step: str, ok: bool, detail: str, ms: int) -> None:
        steps.append({"段階": step, "結果": "OK" if ok else "NG", "詳細": detail, "ms": ms})

    # DNS
    t0 = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        rec("DNS 解決", True, ", ".join(ips), int((time.perf_counter() - t0) * 1000))
    except OSError as exc:
        rec("DNS 解決", False, str(exc), int((time.perf_counter() - t0) * 1000))
        return steps

    # TCP
    t0 = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        rec("TCP 接続", True, f"{host}:{port}", int((time.perf_counter() - t0) * 1000))
    except OSError as exc:
        rec("TCP 接続", False, str(exc), int((time.perf_counter() - t0) * 1000))
        return steps

    # TLS
    if u.scheme == "https":
        t0 = time.perf_counter()
        try:
            ctx = ssl.create_default_context()
            tls = ctx.wrap_socket(sock, server_hostname=host)
            cert = tls.getpeercert() or {}
            subj = dict(x[0] for x in cert.get("subject", ())) if cert else {}
            rec("TLS ハンドシェイク", True, f"{tls.version()} / CN={subj.get('commonName', '?')} / 有効期限 {cert.get('notAfter', '?')}", int((time.perf_counter() - t0) * 1000))
            tls.close()
        except (ssl.SSLError, OSError) as exc:
            rec("TLS ハンドシェイク", False, f"{type(exc).__name__}: {exc}", int((time.perf_counter() - t0) * 1000))
            try:
                sock.close()
            except OSError:
                pass
            return steps
    else:
        sock.close()

    # HTTP
    for ua_label, ua in (("ブラウザ風 UA", None), ("UA なし", "")):
        t0 = time.perf_counter()
        try:
            headers = {} if ua is None else {"User-Agent": ua}
            r = requests.get(base_url.rstrip("/") + "/", headers=headers, timeout=timeout, allow_redirects=True)
            rec(f"HTTP GET / ({ua_label})", True, f"HTTP {r.status_code} / {r.headers.get('Server', '')} / {r.headers.get('Content-Type', '')[:40]} / 最終URL {r.url}", int((time.perf_counter() - t0) * 1000))
        except requests.RequestException as exc:
            rec(f"HTTP GET / ({ua_label})", False, str(exc)[:200], int((time.perf_counter() - t0) * 1000))
    return steps
