"""Link-validation gate (2026-07-16): the RandGallery shutdown article shipped
three invented urls to the live feed — downbad.art (real site: downbad.farm,
the name came from research but the domain was guessed), alchemon.com (the
project never appeared in the research trace at all), and a guessed
algorand.foundation/ecosystem-projects/… page that 404s. The numeric
gatekeeper can't see urls. The gate delinks (anchor text survives) any body
url that neither appeared in the research trace nor resolves live.
"""

from __future__ import annotations

import pytest

from app.modules.newspaper import link_gate
from app.modules.newspaper.link_gate import sanitize_untraced_links

_TRACE = [
    {
        "tool": "search_web",
        "arguments": {"query": "algorand nft"},
        "result": {"results": [{"url": "https://dartroom.xyz/", "title": "Dartroom"}]},
    },
    {
        "tool": "fetch_url",
        "arguments": {"url": "https://www.randgallery.com/"},
        "result": {"text": "marketplace page"},
    },
]


@pytest.fixture(autouse=True)
def _gate_enabled(monkeypatch):
    monkeypatch.setattr("app.core.config.LINK_GATE_ENABLED", True, raising=False)


def test_traced_links_kept_without_any_network_check(monkeypatch) -> None:
    # x.com/reddit-style hosts block server fetches while being fine for
    # readers — a traced link must never depend on a live check.
    monkeypatch.setattr(
        link_gate,
        "_link_is_live",
        lambda url: pytest.fail("traced links must not be live-checked"),
    )
    payload = {
        "body": "See [Dartroom](https://dartroom.xyz/) and "
        "[Rand Gallery](https://randgallery.com/)."
    }
    out = sanitize_untraced_links(payload, _TRACE)
    assert "[Dartroom](https://dartroom.xyz/)" in out["body"]
    # www./trailing-slash/scheme differences must not defeat trace matching.
    assert "[Rand Gallery](https://randgallery.com/)" in out["body"]
    assert "_links_removed" not in out


def test_untraced_dead_links_are_delinked_but_text_survives(monkeypatch) -> None:
    monkeypatch.setattr(link_gate, "_link_is_live", lambda url: False)
    payload = {
        "body": "Alternatives include [Downbad](https://downbad.art/) and "
        "[Alchemon](https://alchemon.com/), plus "
        "[Al Goanna](https://algorand.foundation/ecosystem-projects/al-goanna)."
    }
    out = sanitize_untraced_links(payload, _TRACE)
    assert out["body"] == (
        "Alternatives include Downbad and Alchemon, plus Al Goanna."
    )
    assert out["_links_removed"] == [
        "https://downbad.art/",
        "https://alchemon.com/",
        "https://algorand.foundation/ecosystem-projects/al-goanna",
    ]


def test_untraced_but_live_links_are_kept(monkeypatch) -> None:
    monkeypatch.setattr(link_gate, "_link_is_live", lambda url: True)
    payload = {"body": "See [the docs](https://developer.algorand.org/docs)."}
    out = sanitize_untraced_links(payload, _TRACE)
    assert "[the docs](https://developer.algorand.org/docs)" in out["body"]
    assert "_links_removed" not in out


def test_live_check_budget_keeps_links_past_the_cap(monkeypatch) -> None:
    checks = {"n": 0}

    def fake_live(url):
        checks["n"] += 1
        return False

    monkeypatch.setattr(link_gate, "_link_is_live", fake_live)
    monkeypatch.setattr(link_gate, "_MAX_LIVE_CHECKS", 2)
    body = " ".join(f"[l{i}](https://dead{i}.example/)" for i in range(4))
    out = sanitize_untraced_links({"body": body}, [])
    assert checks["n"] == 2
    # First two delinked; the rest kept as-is rather than stalling compose.
    assert out["body"].startswith("l0 l1 ")
    assert "[l2](https://dead2.example/)" in out["body"]


def test_gate_disabled_is_a_noop(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.LINK_GATE_ENABLED", False, raising=False)
    monkeypatch.setattr(
        link_gate, "_link_is_live", lambda url: pytest.fail("disabled gate fetched")
    )
    body = "[dead](https://downbad.art/)"
    out = sanitize_untraced_links({"body": body}, [])
    assert out["body"] == body


def test_duplicate_urls_checked_once(monkeypatch) -> None:
    checks = {"n": 0}

    def fake_live(url):
        checks["n"] += 1
        return False

    monkeypatch.setattr(link_gate, "_link_is_live", fake_live)
    body = "[a](https://dead.example/) then [b](https://dead.example/)"
    out = sanitize_untraced_links({"body": body}, [])
    assert checks["n"] == 1
    assert out["body"] == "a then b"
