"""mark_breaking_news tool (2026-07-17): replaces the deterministic keyword
classifier that mistagged ordinary positive infrastructure claims (a
"zero downtime" interview got tagged NETWORK_INCIDENT and shipped as
"Breaking:" about a months-stale campaign, plus 4 more live articles hit
the same "downtime" false-positive). The writer decides now, via this tool,
after having actually researched the story.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.ai.breaking_news_tool import (
    MARK_BREAKING_NEWS_SCHEMA,
    breaking_reason_from_trace,
    mark_breaking_news_handler,
)
from app.modules.newspaper.publish_policy import PublishTier
from app.modules.newspaper.tasks import publish_tasks as pt


def test_handler_returns_marked_and_trims_reason() -> None:
    result = mark_breaking_news_handler(reason="x" * 5000)
    assert result["marked_breaking"] is True
    assert len(result["reason"]) <= 400


def test_schema_requires_reason() -> None:
    props = MARK_BREAKING_NEWS_SCHEMA["function"]["parameters"]
    assert props["required"] == ["reason"]


def test_registered_in_writer_tool_registry() -> None:
    from app.modules.ai.writer_tools import all_tools

    schemas, handlers = all_tools(context={"service_id": "x", "source_url": "x", "model": "m"})
    names = {(s.get("function") or {}).get("name") for s in schemas}
    assert "mark_breaking_news" in names
    assert "mark_breaking_news" in handlers


def test_breaking_reason_from_trace_finds_the_call() -> None:
    reason = "consensus halted per official status page"
    trace = [
        {"tool": "search_web", "arguments": {}, "result": {}},
        {
            "tool": "mark_breaking_news",
            "arguments": {"reason": reason},
            "result": {"marked_breaking": True, "reason": reason},
        },
    ]
    assert breaking_reason_from_trace(trace) == reason


def test_breaking_reason_from_trace_none_when_never_called() -> None:
    trace = [{"tool": "search_web", "arguments": {}, "result": {}}]
    assert breaking_reason_from_trace(trace) is None


def test_breaking_reason_from_trace_ignores_other_tools_named_similarly() -> None:
    trace = [{"tool": "review_draft", "arguments": {}, "result": {"marked_breaking": True}}]
    assert breaking_reason_from_trace(trace) is None


def test_breaking_reason_from_trace_takes_the_last_call() -> None:
    trace = [
        {
            "tool": "mark_breaking_news",
            "arguments": {"reason": "first guess"},
            "result": {"marked_breaking": True, "reason": "first guess"},
        },
        {
            "tool": "mark_breaking_news",
            "arguments": {"reason": "confirmed after more research"},
            "result": {"marked_breaking": True, "reason": "confirmed after more research"},
        },
    ]
    assert breaking_reason_from_trace(trace) == "confirmed after more research"


def test_writer_flagged_breaking_upgrades_standard_tier() -> None:
    composed = SimpleNamespace(breaking_reason="consensus halted per status page")
    assert pt._writer_flagged_breaking(PublishTier.STANDARD, composed) is True


def test_writer_flagged_breaking_false_without_a_reason() -> None:
    composed = SimpleNamespace(breaking_reason=None)
    assert pt._writer_flagged_breaking(PublishTier.STANDARD, composed) is False


def test_writer_flagged_breaking_false_when_already_breaking() -> None:
    # No double-application / no misleading log line when tier is already
    # breaking (e.g. a future re-enablement of BREAKING_TIER_ENABLED).
    composed = SimpleNamespace(breaking_reason="consensus halted per status page")
    assert pt._writer_flagged_breaking(PublishTier.BREAKING, composed) is False


def test_writer_flagged_breaking_tolerates_missing_attribute() -> None:
    # ArticleComposeResult predates breaking_reason on some construction
    # paths (weekly digest) — getattr must not raise.
    composed = SimpleNamespace()
    assert pt._writer_flagged_breaking(PublishTier.STANDARD, composed) is False
