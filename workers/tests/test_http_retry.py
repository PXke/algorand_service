import httpx

from app.modules.scraper.core.http_retry import request_with_retry


def test_retry_after_on_429() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        resp = request_with_retry(client, "GET", "https://example.com/test", max_attempts=3)
    assert resp.status_code == 200
    assert calls["n"] == 2
