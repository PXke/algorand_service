"""confirm_alert_topic: keyword topic classification demotes to a routing
hint; reader-facing alert tags and the scam-topic match-key carve-out require
the writer's confirmation.

Root incident (2026-07-18): classify_publish_topic tagged the Algorand
Foundation's own homepage rebrand SCAM_ALERT — a quoted 2021 research paper
asked "is this approach vulnerable to malicious servers?", and "malicious"
near ordinary "opt-in" vocabulary satisfied the context+alarm scan. Second
false scam labeling in a week; same fix shape as mark_breaking_news."""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.ai.alert_topic_tool import (
    confirm_alert_topic_handler,
    confirmed_alert_from_trace,
)
from app.modules.newspaper.publish_policy import PublishTopic
from app.modules.newspaper.tasks.publish_tasks import _effective_alert_topic


def test_handler_accepts_known_kinds_and_rejects_others():
    ok = confirm_alert_topic_handler(kind="scam_alert", reason="drainer live")
    assert ok == {"confirmed": True, "kind": "scam_alert", "reason": "drainer live"}
    bad = confirm_alert_topic_handler(kind="urgent", reason="x")
    assert bad["confirmed"] is False


def test_trace_scan_returns_last_confirmed_kind():
    trace = [
        {"tool": "fetch_url", "result": {}},
        {"tool": "confirm_alert_topic", "result": {"confirmed": True, "kind": "scam_alert"}},
    ]
    assert confirmed_alert_from_trace(trace) == "scam_alert"
    assert confirmed_alert_from_trace([]) is None
    assert confirmed_alert_from_trace(None) is None
    # A rejected call (bad kind) never confirms.
    assert (
        confirmed_alert_from_trace(
            [{"tool": "confirm_alert_topic", "result": {"confirmed": False}}]
        )
        is None
    )


def test_unconfirmed_scam_topic_downgrades_to_generic():
    """The incident pin: keyword-routed scam_alert with no writer
    confirmation must not keep its reader-facing consequences."""
    composed = SimpleNamespace(confirmed_alert=None)
    assert _effective_alert_topic(PublishTopic.SCAM_ALERT, composed) is PublishTopic.GENERIC
    assert (
        _effective_alert_topic(PublishTopic.NETWORK_INCIDENT, composed)
        is PublishTopic.GENERIC
    )


def test_confirmed_alert_keeps_topic():
    composed = SimpleNamespace(confirmed_alert="scam_alert")
    assert _effective_alert_topic(PublishTopic.SCAM_ALERT, composed) is PublishTopic.SCAM_ALERT


def test_writer_kind_wins_over_keyword_route():
    """Keyword said scam, writer (who read the material) said incident."""
    composed = SimpleNamespace(confirmed_alert="network_incident")
    assert (
        _effective_alert_topic(PublishTopic.SCAM_ALERT, composed)
        is PublishTopic.NETWORK_INCIDENT
    )


def test_non_alert_topics_pass_through_untouched():
    composed = SimpleNamespace(confirmed_alert=None)
    for topic in (PublishTopic.GENERIC, PublishTopic.SDK_RELEASE, PublishTopic.PRICING_CHANGE):
        assert _effective_alert_topic(topic, composed) is topic


def test_tool_registered_in_writer_toolset():
    from app.modules.ai.writer_tools import all_tools

    schemas, handlers = all_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "confirm_alert_topic" in names
    assert "confirm_alert_topic" in handlers
