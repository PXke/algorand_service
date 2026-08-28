"""workers/scratch/backfill_translated_titles.py: derives translated_titles from each article's existing translations map, dry-run makes no writes, a real run merges only the missing languages."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scratch" / "backfill_translated_titles.py"


def _load_script() -> ModuleType:
    """Import the one-off script as a module (it lives outside the `app` package, so it's not on pythonpath)."""
    spec = importlib.util.spec_from_file_location("backfill_translated_titles", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _translation_json(title: str, summary: str, body: str = "corps") -> str:
    return json.dumps({"title": title, "summary": summary, "body": body}, ensure_ascii=False)


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """A freshly-loaded copy of the backfill script, with list_feed_articles/get_article stubbed to four fake published articles covering every branch: needs backfill, already current, no usable translations, and a row that no longer resolves."""
    from app.modules.newspaper.article_store import ArticleDetail, FeedArticleRow

    mod = _load_script()

    rows = [
        FeedArticleRow(
            article_id="a1", service_id="svc", title="Needs backfill", summary="s",
            published_at_epoch=1,
        ),
        FeedArticleRow(
            article_id="a2", service_id="svc", title="Already current", summary="s",
            published_at_epoch=2,
        ),
        FeedArticleRow(
            article_id="a3", service_id="svc", title="No translations", summary="s",
            published_at_epoch=3,
        ),
        FeedArticleRow(
            article_id="a4", service_id="svc", title="Gone", summary="s",
            published_at_epoch=4,
        ),
    ]  # fmt: skip
    details = {
        "a1": ArticleDetail(
            article_id="a1",
            service_id="svc",
            title="Needs backfill",
            summary="s",
            body="b",
            published_at_epoch=1,
            trigger_txid="",
            trigger_round=0,
            source_url="https://example.com",
            translations={
                "fr": _translation_json("Titre francais", "Resume francais"),
                "es": _translation_json("Titulo espanol", "Resumen espanol"),
            },
            translated_titles=None,
        ),
        "a2": ArticleDetail(
            article_id="a2",
            service_id="svc",
            title="Already current",
            summary="s",
            body="b",
            published_at_epoch=2,
            trigger_txid="",
            trigger_round=0,
            source_url="https://example.com",
            translations={"fr": _translation_json("Titre", "Resume")},
            translated_titles={"fr": json.dumps({"title": "Titre", "summary": "Resume"})},
        ),
        "a3": ArticleDetail(
            article_id="a3",
            service_id="svc",
            title="No translations",
            summary="s",
            body="b",
            published_at_epoch=3,
            trigger_txid="",
            trigger_round=0,
            source_url="https://example.com",
            translations=None,
        ),
    }

    monkeypatch.setattr(mod, "list_feed_articles", lambda limit: rows)  # noqa: ARG005
    monkeypatch.setattr(mod, "get_article", lambda article_id: details.get(article_id))
    return mod


def test_derive_translated_titles_drops_body_keeps_title_and_summary() -> None:
    """Derives the lightweight {title, summary} shape from a full {title, summary, body} translation, per language."""
    from app.modules.newspaper.article_store import ArticleDetail, FeedArticleRow  # noqa: F401

    mod = _load_script()
    translations = {
        "fr": _translation_json("Titre francais", "Resume francais", body="Corps du texte"),
    }

    derived = mod.derive_translated_titles(translations)

    assert set(derived.keys()) == {"fr"}
    parsed = json.loads(derived["fr"])
    assert parsed == {"title": "Titre francais", "summary": "Resume francais"}
    assert "Corps du texte" not in derived["fr"]


def test_derive_translated_titles_skips_unparseable_entries() -> None:
    """A malformed translation JSON entry is skipped, not raised."""
    mod = _load_script()

    derived = mod.derive_translated_titles({"fr": "not json", "es": "[]"})

    assert derived == {}


def test_dry_run_makes_no_cassandra_writes(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run reports what WOULD be backfilled without calling update_article_translations."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        script, "update_article_translations", lambda *a, **kw: calls.append((a, kw)) or True
    )
    monkeypatch.setattr(sys, "argv", ["backfill_translated_titles.py", "--dry-run"])

    script.main()

    assert calls == []
    out = capsys.readouterr().out
    assert "would backfill a1" in out
    assert "DRY_RUN_DONE" in out


def test_real_run_merges_only_missing_languages(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real run writes derived translated_titles for a1's two missing languages, skips a2 (already current), a3 (no translations), and a4 (no longer resolves)."""
    calls: list[dict] = []

    def fake_update(article_id: str, translations: dict, translated_titles: dict) -> bool:
        calls.append(
            {
                "article_id": article_id,
                "translations": translations,
                "translated_titles": translated_titles,
            }
        )
        return True

    monkeypatch.setattr(script, "update_article_translations", fake_update)
    monkeypatch.setattr(sys, "argv", ["backfill_translated_titles.py"])

    script.main()

    assert len(calls) == 1
    call = calls[0]
    assert call["article_id"] == "a1"
    # translations is passed empty -- this backfill never touches the
    # already-correct translations map, only translated_titles.
    assert call["translations"] == {}
    assert set(call["translated_titles"].keys()) == {"fr", "es"}
    assert json.loads(call["translated_titles"]["fr"]) == {
        "title": "Titre francais",
        "summary": "Resume francais",
    }

    out = capsys.readouterr().out
    assert "1 needed backfilling" in out
    assert "1 already current" in out
    assert "backfilled=1" in out
    assert "BACKFILL_DONE" in out
    assert "ANOMALY a4" in out


def test_already_current_article_is_never_written(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An article whose translated_titles already covers every stored translation language is left untouched."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        script, "update_article_translations", lambda *a, **kw: calls.append((a, kw)) or True
    )
    monkeypatch.setattr(sys, "argv", ["backfill_translated_titles.py"])

    script.main()

    assert all(c[0][0] != "a2" for c in calls)
