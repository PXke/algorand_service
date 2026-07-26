#!/usr/bin/env python3
"""Local browser bridge — use *your* logged-in Chrome/Edge for Discord/Telegram.

You stay signed in on your machine. This script snapshots visible text and POSTs
to the platform ingest API (same queue as manual push). No worker Playwright,
no bots on official servers.

Setup:
  1. Start Chrome with remote debugging:
       google-chrome --remote-debugging-port=9222
     (or Chromium / Edge equivalent)
  2. Open Discord + Telegram channel tabs and log in.
  3. Copy targets.example.json → targets.json, set api_base + ingest_key.
  4. pip install -r requirements.txt && playwright install chromium
  5. Run: python bridge.py watch

One-shot: python bridge.py snapshot
Manual:   python bridge.py push --service-id x --title "..." --text-file snap.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_CDP = "http://127.0.0.1:9222"
DEFAULT_CONFIG = Path(__file__).resolve().parent / "targets.json"
STATE_DIR = Path(os.environ.get("ALGORAND_BRIDGE_STATE", Path.home() / ".cache/algorand-bridge"))


def _load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        print(f"Missing config: {path}", file=sys.stderr)
        print("Copy targets.example.json to targets.json and edit it.", file=sys.stderr)
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("targets.json must be a JSON object")
    return data


def _state_path() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / "content_hashes.json"


def _load_hashes() -> dict[str, str]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_hashes(hashes: dict[str, str]) -> None:
    _state_path().write_text(json.dumps(hashes, indent=2), encoding="utf-8")


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def _clean_text(text: str) -> str:
    lines: list[str] = []
    prev = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) < 2:
            continue
        if line == prev:
            continue
        lines.append(line)
        prev = line
    return "\n".join(lines[-300:])


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _post_signal(
    *,
    api_base: str,
    ingest_key: str,
    payload: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = api_base.rstrip("/") + "/api/v1/ingest/signal"
    headers = {"Content-Type": "application/json", "X-Ingest-Key": ingest_key}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            return {"status": "queued"}
        return body


def push_payload(config: dict[str, Any], payload: dict[str, Any], *, force: bool = False) -> bool:
    api_base = str(config.get("api_base", "")).strip()
    ingest_key = str(config.get("ingest_key", "")).strip() or os.environ.get("INGEST_API_KEY", "")
    if not api_base or not ingest_key:
        print("Set api_base and ingest_key in targets.json (or INGEST_API_KEY).", file=sys.stderr)
        return False

    service_id = str(payload["service_id"]).strip()
    page_text = str(payload["page_text"]).strip()
    if len(page_text) < 40:
        print(f"Skip {service_id}: text too short ({len(page_text)} chars)", file=sys.stderr)
        return False

    digest = _content_hash(page_text)
    hashes = _load_hashes()
    if not force and hashes.get(service_id) == digest:
        print(f"Unchanged: {service_id}")
        return False

    body = _post_signal(api_base=api_base, ingest_key=ingest_key, payload=payload)
    hashes[service_id] = digest
    _save_hashes(hashes)
    depth = body.get("depth", "?")
    print(f"Pushed {service_id} → queue depth {depth}")
    return True


def _find_or_open_page(context, target_url: str):
    want = _normalize_url(target_url)
    for page in context.pages:
        if _normalize_url(page.url).startswith(want) or want in _normalize_url(page.url):
            return page
    page = context.new_page()
    page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(int(os.environ.get("BRIDGE_WAIT_MS", "3000")))
    return page


def _snapshot_page_text(page) -> tuple[str, str]:
    title = page.title() or "Channel snapshot"
    selectors = ("main", "article", "[role='main']", "[class*='messages']", "[class*='chat']")
    chunks: list[str] = []
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            try:
                chunk = locator.first.inner_text(timeout=3000)
            except Exception:
                continue
            if chunk and len(chunk.strip()) > 80:
                chunks.append(chunk.strip())
    text = "\n\n".join(chunks) if chunks else page.inner_text("body")
    return title.strip(), _clean_text(text)


def snapshot_targets(config: dict[str, Any], *, force: bool = False) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install: pip install playwright && playwright install chromium", file=sys.stderr)
        return 1

    cdp = str(config.get("cdp_url", DEFAULT_CDP)).strip() or DEFAULT_CDP
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets:
        print("No targets in config.", file=sys.stderr)
        return 1

    pushed = 0
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp)
        except Exception as exc:
            print(
                f"Cannot connect to browser at {cdp}: {exc}\n"
                "Start Chrome with: google-chrome --remote-debugging-port=9222",
                file=sys.stderr,
            )
            return 1

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        for entry in targets:
            if not isinstance(entry, dict):
                continue
            service_id = str(entry.get("service_id", "")).strip()
            page_url = str(entry.get("url", "")).strip()
            if not service_id or not page_url:
                continue

            page = _find_or_open_page(context, page_url)
            page_title, page_text = _snapshot_page_text(page)
            if len(page_text) < 40:
                print(f"Skip {service_id}: could not read enough text from {page.url}", file=sys.stderr)
                continue

            payload = {
                "service_id": service_id,
                "display_name": str(entry.get("display_name", service_id)),
                "page_title": str(entry.get("page_title", page_title))[:512],
                "page_text": page_text[:100_000],
                "source_url": str(entry.get("source_url", page.url))[:2048],
                "source_kind": "local_browser",
                "match_kind": "local_snapshot",
                "match_value": _content_hash(page_text)[:16],
            }
            if push_payload(config, payload, force=force):
                pushed += 1

    return 0 if pushed >= 0 else 1


def cmd_push(args: argparse.Namespace) -> int:
    config = _load_config(Path(args.config))
    text = args.text or ""
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    payload = {
        "service_id": args.service_id,
        "display_name": args.display_name or args.service_id,
        "page_title": args.title,
        "page_text": text,
        "source_url": args.source_url or f"local://{args.service_id}",
        "source_kind": "local_browser",
    }
    return 0 if push_payload(config, payload, force=args.force) else 1


def cmd_snapshot(args: argparse.Namespace) -> int:
    config = _load_config(Path(args.config))
    return snapshot_targets(config, force=args.force)


def cmd_watch(args: argparse.Namespace) -> int:
    config = _load_config(Path(args.config))
    interval = int(config.get("poll_seconds", args.interval))
    print(f"Watching every {interval}s — Ctrl+C to stop")
    while True:
        snapshot_targets(config, force=args.force)
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local browser → platform ingest bridge")
    parser.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="Path to targets.json")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="One-shot snapshot all targets")
    snap.add_argument("--force", action="store_true", help="Push even if content hash unchanged")
    snap.set_defaults(func=cmd_snapshot)

    watch = sub.add_parser("watch", help="Poll snapshots on an interval")
    watch.add_argument("--interval", type=int, default=0, help="Override poll_seconds from config")
    watch.add_argument("--force", action="store_true")
    watch.set_defaults(func=cmd_watch)

    push = sub.add_parser("push", help="Manual push (paste / file)")
    push.add_argument("--service-id", required=True)
    push.add_argument("--display-name", default="")
    push.add_argument("--title", default="Manual snapshot")
    push.add_argument("--text", default="")
    push.add_argument("--text-file", default="")
    push.add_argument("--source-url", default="")
    push.add_argument("--force", action="store_true")
    push.set_defaults(func=cmd_push)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
