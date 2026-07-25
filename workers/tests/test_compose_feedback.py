"""report_compose_issue — writer pipeline feedback tool."""

import pytest

from app.modules.ai import writer_tools as wt


def test_report_compose_issue_registered() -> None:
    """The report_compose_issue tool is registered in both the schemas and handlers."""
    schemas, handlers = wt.all_tools()
    names = {(s.get("function") or {}).get("name") for s in schemas}
    assert "report_compose_issue" in names
    assert "report_compose_issue" in handlers


def test_handler_records_valid_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid feedback is recorded with its category, severity, and the handler's bound source_url."""
    recorded: list[dict] = []

    def _fake_record(**kwargs: object) -> bool:
        recorded.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_compose_feedback",
        _fake_record,
    )
    handler = wt._make_report_compose_issue_handler(
        {"service_id": "svc", "source_url": "https://x/", "model": "mistral-small"}
    )
    out = handler(
        category="source_data",
        summary="Source page is a cookie banner with no article body",
        detail="fetch_url returned 400 chars of nav boilerplate only",
        severity="high",
    )
    assert out["ok"] is True
    assert recorded[0]["category"] == "source_data"
    assert recorded[0]["severity"] == "high"
    assert recorded[0]["source_url"] == "https://x/"


def test_handler_rejects_empty_summary() -> None:
    """A whitespace-only summary is rejected."""
    handler = wt._make_report_compose_issue_handler({})
    out = handler(category="prompt", summary="   ")
    assert out["ok"] is False


def test_handler_rejects_invalid_category() -> None:
    """A category outside the allowed set is rejected with an "invalid category" error."""
    handler = wt._make_report_compose_issue_handler({})
    out = handler(category="tool_gap", summary="need telegram search")
    assert out["ok"] is False
    assert "invalid category" in out["error"]


def test_tools_guidance_mentions_pipeline_feedback() -> None:
    """The writer's tools-guidance prompt text documents the pipeline-feedback tool."""
    import app.modules.ai.mistral_compose as mc

    assert "report_compose_issue" in mc._TOOLS_GUIDANCE
    assert "PIPELINE FEEDBACK" in mc._TOOLS_GUIDANCE
