"""predict_publish_proba must support both the legacy scalar model and the new
text-aware model (TF-IDF vectorizer + scalar subscores)."""

from app.modules.newspaper import grader_model as gm


def _subs(v: float = 0.5) -> dict:
    return dict.fromkeys(gm.FEATURE_ORDER, v)


def test_scalar_model_path(monkeypatch) -> None:
    class _M:
        def predict_proba(self, x):
            return [[0.3, 0.7]]

    monkeypatch.setattr(gm, "_load_model", lambda: _M())
    assert abs(gm.predict_publish_proba(_subs()) - 0.7) < 1e-9


def test_text_aware_model_path(monkeypatch) -> None:
    import pytest

    pytest.importorskip("sklearn")
    from sklearn.feature_extraction.text import TfidfVectorizer

    vec = TfidfVectorizer().fit(["algorand mainnet upgrade", "defi staking rewards"])

    class _MT:
        def predict_proba(self, x):
            # x must be the hstacked (text | scalar) matrix.
            assert x.shape[1] == len(vec.get_feature_names_out()) + len(gm.FEATURE_ORDER)
            return [[0.1, 0.9]]

    monkeypatch.setattr(gm, "_load_model", lambda: {"vectorizer": vec, "model": _MT()})
    assert abs(gm.predict_publish_proba(_subs(), "algorand mainnet upgrade") - 0.9) < 1e-9


def test_none_when_untrained(monkeypatch) -> None:
    monkeypatch.setattr(gm, "_load_model", lambda: None)
    assert gm.predict_publish_proba(_subs(), "anything") is None
