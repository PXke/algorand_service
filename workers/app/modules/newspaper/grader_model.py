"""Learned article grader: logistic regression on the captured grade dimensions
→ P(approved). Trains from `classifier_feedback` rows whose metadata snapshotted
the grade dimensions (see admin _grade_meta_for_review). Until there's enough
balanced data it stays untrained and the heuristic weighted sum is used — so it
starts rough and visibly improves as labels accumulate.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

# Fixed feature order — MUST match grade_article_draft's subscores keys.
# The sklearn grader is retired from the grade path (the ModernBERT quality head
# + heuristic floor replace it); popularity was removed as a grading signal.
FEATURE_ORDER = (
    "novelty",
    "relevance",
    "recency",
    "length",
    "specificity",
    "structure",
)


def _model_path() -> Path:
    from app.core.config import GRADER_MODEL_PATH

    return Path(GRADER_MODEL_PATH)


def _load_model():
    path = _model_path()
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None


def _features(subscores: dict) -> list[float]:
    return [float(subscores.get(k, 0.0)) for k in FEATURE_ORDER]


def predict_publish_proba(subscores: dict, text: str = "") -> float | None:
    """P(approved) from the learned model, or None when untrained (use heuristic).

    Two model shapes are supported: a plain scalar model (legacy: 7 subscores
    only) and a text-aware model (dict with a fitted ``vectorizer`` — TF-IDF of
    the article body hstacked with the subscores), so the grader can learn from
    the article's actual words, not just the heuristic dimension scores."""
    model = _load_model()
    if model is None:
        return None
    try:
        if isinstance(model, dict) and model.get("vectorizer") is not None:
            from scipy.sparse import csr_matrix, hstack

            x_text = model["vectorizer"].transform([text or ""])
            x_scalar = csr_matrix([_features(subscores)])
            x = hstack([x_text, x_scalar])
            return float(model["model"].predict_proba(x)[0][1])
        return float(model.predict_proba([_features(subscores)])[0][1])
    except Exception:
        return None


def _training_rows(limit: int) -> tuple[list[list[float]], list[str], list[int]]:
    """Pull (scalar_features, article_text, label) from classifier_feedback rows
    that captured the grade dimensions. ``article_text`` is "" for rows labelled
    before article-text capture (2026-06-18) — those train scalar-only."""
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    index = session.execute(
        "SELECT feedback_id FROM classifier_feedback_by_time WHERE bucket = %s LIMIT %s",
        ("main", limit),
    )
    scalars: list[list[float]] = []
    texts: list[str] = []
    y: list[int] = []
    for idx in index:
        row = session.execute(
            "SELECT approved, metadata FROM classifier_feedback WHERE feedback_id = %s",
            (idx.feedback_id,),
        ).one()
        if row is None or not row.metadata:
            continue
        meta = dict(row.metadata)
        detail = meta.get("grade_detail")
        if not detail:
            continue
        try:
            parsed = json.loads(detail) if isinstance(detail, str) else detail
            subs = dict(parsed.get("subscores", parsed))
            # Human corrections (0-10 scale) override the auto-scores as truth.
            corrected = meta.get("corrected_scores")
            if corrected:
                cdict = json.loads(corrected) if isinstance(corrected, str) else corrected
                for k, v in cdict.items():
                    subs[k] = float(v) / 10.0  # subscores are 0-1
            scalars.append([float(subs[k]) for k in FEATURE_ORDER])
            texts.append(str(meta.get("article_text", "") or ""))
            y.append(1 if row.approved else 0)
        except Exception:
            continue
    return scalars, texts, y


def train_grader(*, limit: int = 1000) -> dict:
    """Retrain the learned grader from captured feedback. No-op (keeps heuristic)
    until there are enough samples with both classes present. Goes text-aware
    once enough rows carry the article body, else stays scalar-only."""
    from app.core.config import GRADER_MIN_SAMPLES, GRADER_TEXT_MIN_SAMPLES

    scalars, texts, y = _training_rows(limit)
    n_pos = sum(y)
    n_neg = len(y) - n_pos
    if len(y) < GRADER_MIN_SAMPLES or n_pos == 0 or n_neg == 0:
        return {
            "status": "skipped",
            "reason": "insufficient_or_unbalanced_data",
            "samples": len(y),
            "approved": n_pos,
            "rejected": n_neg,
            "min_samples": GRADER_MIN_SAMPLES,
        }
    try:
        from sklearn.linear_model import LogisticRegression

        n_text = sum(1 for t in texts if t.strip())
        path = _model_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        if n_text >= GRADER_TEXT_MIN_SAMPLES:
            # Text-aware: TF-IDF of the article body + the heuristic subscores.
            # Strong L2 (small C) + capped vocabulary to curb overfit on a small
            # corpus; the model can finally learn WHY from the words, not just
            # the dimension scores.
            from scipy.sparse import csr_matrix, hstack
            from sklearn.feature_extraction.text import TfidfVectorizer

            vectorizer = TfidfVectorizer(
                max_features=1000, ngram_range=(1, 2), min_df=2, stop_words="english"
            )
            x_text = vectorizer.fit_transform([t or "" for t in texts])
            x_scalar = csr_matrix(scalars)
            x = hstack([x_text, x_scalar])
            model = LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5)
            model.fit(x, y)
            acc = float(model.score(x, y))
            with path.open("wb") as fh:
                pickle.dump({"vectorizer": vectorizer, "model": model, "version": 3}, fh)
            # Report the scalar-dimension coefficients (the tail of the vector).
            tail = model.coef_[0][-len(FEATURE_ORDER):]
            weights = dict(zip(FEATURE_ORDER, (round(float(c), 3) for c in tail), strict=False))
            return {
                "status": "trained",
                "mode": "text+scalar",
                "samples": len(y),
                "text_samples": n_text,
                "approved": n_pos,
                "rejected": n_neg,
                "features": int(x.shape[1]),
                "train_accuracy": round(acc, 3),
                "weights": weights,
            }

        model = LogisticRegression(max_iter=1000, class_weight="balanced")
        model.fit(scalars, y)
        acc = float(model.score(scalars, y))
        with path.open("wb") as fh:
            pickle.dump(model, fh)
        weights = dict(zip(FEATURE_ORDER, (round(c, 3) for c in model.coef_[0]), strict=False))
        return {
            "status": "trained",
            "mode": "scalar",
            "samples": len(y),
            "text_samples": n_text,
            "approved": n_pos,
            "rejected": n_neg,
            "train_accuracy": round(acc, 3),
            "weights": weights,
            "note": f"scalar-only until {GRADER_TEXT_MIN_SAMPLES} labelled rows carry article text",
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)[:200], "samples": len(y)}


def grader_status() -> dict:
    """Lightweight status for the training dashboard."""
    return {"trained": _model_path().exists(), "model_path": str(_model_path())}
