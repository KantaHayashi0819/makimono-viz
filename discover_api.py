#!/usr/bin/env python3
"""Makimono API 仕様探索スクリプト。

利用者の PC から実行し、API キーで dashboard.php や代表的なエンドポイントへアクセスして
応答を調査、結果を discovery_report.json に保存します。

    python discover_api.py                 # .env の設定で実行
    python discover_api.py --base https://makimono.elab.one --key XXXX

出力された discovery_report.json（API キーは含まれません）を共有すれば、
アプリ側の接続設定を実際の API 仕様に合わせられます。
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from makimono.api_client import COMMON_ENDPOINTS, MakimonoClient

load_dotenv()

HINT_RE = re.compile(
    r"""(?ix)
    (?:["'(])(/?[\w\-./]*(?:api|ajax|json|export|data)[\w\-./]*(?:\.php)?(?:\?[^"'()\s]*)?)["')]
    | (fetch|axios\.(?:get|post)|\$\.(?:ajax|get|post|getJSON)|XMLHttpRequest)\s*\(
    | (api[_-]?key|apikey|token|x-api-key|authorization)
    """
)


def scan_hints(text: str) -> dict:
    urls, calls, auth_words = set(), set(), set()
    for m in HINT_RE.finditer(text):
        if m.group(1):
            urls.add(m.group(1))
        if m.group(2):
            calls.add(m.group(2))
        if m.group(3):
            auth_words.add(m.group(3).lower())
    scripts = re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", text, re.I)
    forms = re.findall(r"<form[^>]*action=[\"']([^\"']+)[\"']", text, re.I)
    return {"urls": sorted(urls), "calls": sorted(calls), "auth_words": sorted(auth_words), "scripts": scripts, "forms": forms}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("MAKIMONO_BASE_URL", "https://makimono.elab.one"))
    ap.add_argument("--key", default=os.getenv("MAKIMONO_API_KEY", ""))
    ap.add_argument("--out", default="discovery_report.json")
    ap.add_argument("--save-raw", action="store_true", help="HTML/JS 応答を discovery_raw/ に保存")
    args = ap.parse_args()
    if not args.key:
        ap.error("API キーを --key または MAKIMONO_API_KEY で指定してください")

    report: dict = {"base_url": args.base, "pages": {}, "probes": []}
    raw_dir = Path("discovery_raw")
    if args.save_raw:
        raw_dir.mkdir(exist_ok=True)

    auth_variants = [
        ("header X-API-Key", dict(auth_mode="header", header_name="X-API-Key")),
        ("header X-Api-Key", dict(auth_mode="header", header_name="X-Api-Key")),
        ("header Api-Key", dict(auth_mode="header", header_name="Api-Key")),
        ("bearer", dict(auth_mode="bearer")),
        ("query api_key", dict(auth_mode="query", param_name="api_key")),
        ("query apikey", dict(auth_mode="query", param_name="apikey")),
        ("query key", dict(auth_mode="query", param_name="key")),
        ("query token", dict(auth_mode="query", param_name="token")),
        ("none", dict(auth_mode="none")),
    ]

    # 1) dashboard.php 等の HTML を取得し、API のヒントを抽出
    print("== HTML ページの解析 ==")
    base_client = MakimonoClient(args.base, args.key, auth_mode="none")
    seen_scripts: set[str] = set()
    for page in ["dashboard.php", "", "index.php", "api.php", "docs", "api/docs", "swagger.json", "openapi.json", "help.php"]:
        for label, kw in auth_variants[:1] + auth_variants[4:5] + auth_variants[-1:]:
            c = MakimonoClient(args.base, args.key, **kw)
            r = c.request(page)
            entry = {"auth": label, "status": r.status, "json": r.is_json, "content_type": r.content_type, "ms": r.elapsed_ms, "error": r.error, "preview": r.preview(200)}
            if not r.is_json and isinstance(r.body, str):
                entry["hints"] = scan_hints(r.body)
                if args.save_raw and r.ok:
                    (raw_dir / (re.sub(r"[^\w.]+", "_", page or "root") + f"__{label.replace(' ', '_')}.html")).write_text(r.body, encoding="utf-8")
                for s in entry["hints"].get("scripts", []):
                    if s not in seen_scripts and not s.startswith(("http", "//")):
                        seen_scripts.add(s)
            report["pages"].setdefault(page or "/", []).append(entry)
            print(f"  {page or '/':<22} [{label:<18}] -> {r.status} {'JSON' if r.is_json else r.content_type[:30]}")

    # 2) 読み込まれている JS を取得し API 呼び出しのヒントを抽出
    if seen_scripts:
        print("== JavaScript の解析 ==")
        report["scripts"] = {}
        for s in sorted(seen_scripts)[:30]:
            r = base_client.request(s)
            hints = scan_hints(r.body) if isinstance(r.body, str) else {}
            report["scripts"][s] = {"status": r.status, "hints": hints}
            if args.save_raw and r.ok and isinstance(r.body, str):
                (raw_dir / re.sub(r"[^\w.]+", "_", s)).write_text(r.body, encoding="utf-8")
            print(f"  {s:<50} -> {r.status}  urls={len(hints.get('urls', []))}")
            for u in hints.get("urls", []):
                print(f"      {u}")

    # 3) 代表的なエンドポイントを各認証方式で叩く（JSON が返るものを探す）
    print("== エンドポイント探索 ==")
    hinted = {u.split("?")[0].lstrip("/") for p in report["pages"].values() for e in p for u in e.get("hints", {}).get("urls", [])}
    hinted |= {u.split("?")[0].lstrip("/") for v in report.get("scripts", {}).values() for u in v["hints"].get("urls", [])}
    paths = list(dict.fromkeys(COMMON_ENDPOINTS + sorted(hinted)))
    for p in paths:
        best = None
        for label, kw in auth_variants:
            c = MakimonoClient(args.base, args.key, **kw)
            r = c.request(p)
            rec = {"path": p, "auth": label, "status": r.status, "json": r.is_json, "content_type": r.content_type, "ms": r.elapsed_ms, "error": r.error, "preview": r.preview(300)}
            report["probes"].append(rec)
            if r.ok and r.is_json:
                best = rec
                break  # この認証方式で JSON が返った
            if r.status in (401, 403) and best is None:
                best = rec
        if best:
            mark = "✅" if best["json"] and 200 <= best["status"] < 300 else "🔒"
            print(f"  {mark} {p:<34} {best['status']} [{best['auth']}] {best['preview'][:90]}")
        else:
            st = report["probes"][-1]["status"]
            print(f"     {p:<34} {st}")

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = [r for r in report["probes"] if r["json"] and 200 <= r["status"] < 300]
    print(f"\nJSON を返したエンドポイント: {len(ok)} 件 -> {args.out} に保存しました（API キーは含みません）")
    for r in ok[:20]:
        print(f"  {r['path']}  [{r['auth']}]")


if __name__ == "__main__":
    main()
