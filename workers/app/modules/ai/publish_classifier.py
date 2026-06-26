from __future__ import annotations

import hashlib
import pickle
import random
from pathlib import Path
from typing import Any

from app.modules.search.classifier.score import keyword_hits, score_page

_FEATURE_DIM = 8


def _feature_vector(text: str, url: str, category: str) -> list[float]:
    result = score_page(url=url, text=text)
    cat = category.lower()
    cat_bits = [
        1.0 if cat == "news" else 0.0,
        1.0 if cat == "service" else 0.0,
        1.0 if cat in ("tool", "payment") else 0.0,
        1.0 if cat in ("nft", "governance") else 0.0,
    ]
    length = min(1.0, len(text) / 5000.0)
    return [result.score, length, *cat_bits, float(len(text) > 200)]


def _text_blob(text: str, url: str) -> str:
    """Text fed to the TF-IDF vectorizer (url host carries signal too)."""
    return f"{url}\n{text[:6000]}"


def _combined_features(vectorizer, text: str, url: str, category: str):
    """Sparse matrix: TF-IDF of the article text hstacked with the scalar
    features (relevance score, length, category one-hots)."""
    from scipy.sparse import csr_matrix, hstack

    x_text = vectorizer.transform([_text_blob(text, url)])
    x_scalar = csr_matrix([_feature_vector(text, url, category)])
    return hstack([x_text, x_scalar])


def _model_path() -> Path:
    from app.core.config import PUBLISH_CLASSIFIER_MODEL_PATH

    return Path(PUBLISH_CLASSIFIER_MODEL_PATH)


def _load_model() -> Any | None:
    path = _model_path()
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None


def _save_model(model: Any) -> None:
    path = _model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(model, fh)


def _heuristic_publish(text: str, url: str, category: str) -> tuple[bool, float]:
    result = score_page(url=url, text=text)
    score = result.score
    if category in ("news", "governance"):
        score += 0.1
    if category == "generic" and score < 0.4:
        score -= 0.1
    publish = score >= 0.45
    return publish, min(1.0, max(0.0, score))


def predict_publish(text: str, url: str, category: str) -> tuple[bool | None, float]:
    """
    Predict publish-worthiness with its confidence.

    Returns (True/False, confidence) when confident; (None, confidence) when
    manual review is required (low confidence or sampling threshold).
    """
    from app.core.config import (
        CLASSIFIER_CONFIDENCE_THRESHOLD,
        CLASSIFIER_SAMPLING_THRESHOLD,
        PUBLISH_CLASSIFIER_ENABLED,
    )

    if not PUBLISH_CLASSIFIER_ENABLED:
        publish, conf = _heuristic_publish(text, url, category)
    else:
        loaded = _load_model()
        if loaded is None:
            publish, conf = _heuristic_publish(text, url, category)
        else:
            try:
                if isinstance(loaded, dict) and "vectorizer" in loaded:
                    feats = _combined_features(loaded["vectorizer"], text, url, category)
                    model = loaded["model"]
                else:
                    feats = [_feature_vector(text, url, category)]
                    model = loaded
                proba = model.predict_proba(feats)[0]
                publish = bool(model.predict(feats)[0])
                conf = float(max(proba))
            except Exception:
                publish, conf = _heuristic_publish(text, url, category)

    if conf < CLASSIFIER_CONFIDENCE_THRESHOLD:
        return None, conf
    if CLASSIFIER_SAMPLING_THRESHOLD > 0 and random.random() < CLASSIFIER_SAMPLING_THRESHOLD:
        return None, conf
    return publish, conf


def is_publish_worthy(text: str, url: str, category: str) -> bool | None:
    """Backward-compatible wrapper around predict_publish (decision only)."""
    decision, _conf = predict_publish(text, url, category)
    return decision


def relevance_score(text: str, url: str = "") -> float:
    """On-topic relevance in [0, 1] from the page relevance scorer.

    Independent of the publish classifier's review-sampling, so it stays a
    meaningful signal even in training mode (where ``predict_publish`` defers
    everything). Used to gate enqueue and to weight publish-queue priority."""
    try:
        return float(max(0.0, min(1.0, score_page(url=url, text=text).score)))
    except Exception:
        # Fail open: an unscored page is treated as relevant rather than dropped.
        return 1.0


def score_content_for_storage(text: str, url: str = "") -> float:
    """Crude storage-relevance score (~0–10): on-topic keyword families present
    plus the page classifier's 0–1 score scaled ×5. Used for the domain
    relevance_score column / admin sort and the frontier preview_score — not a
    hard gate, so the loose scale is fine."""
    hits = keyword_hits(text)
    if url:
        hits += int(score_page(url=url, text=text).score * 5)
    return float(hits)


def is_content_quality_sufficient(text: str) -> bool:
    """Quality floor: at least 3 distinct on-topic keyword families, or 2 in a
    page long enough to be substantive."""
    hits = keyword_hits(text)
    return hits >= 3 or (len(text) >= 300 and hits >= 2)


def record_classifier_feedback(
    *,
    url: str,
    text_sample: str,
    category: str,
    predicted_category: str | None = None,
    quality: str = "medium",
    predicted_publish: bool,
    approved: bool,
    admin_wallet: str,
    metadata: dict[str, str] | None = None,
) -> str:
    import uuid
    from datetime import UTC, datetime

    from app.core.cassandra import get_cassandra_session

    predicted = (predicted_category or category).strip().lower()
    corrected = category.strip().lower()
    quality_norm = quality.strip().lower()
    feedback_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    session = get_cassandra_session()
    session.execute(
        """
        INSERT INTO classifier_feedback (
          feedback_id, url, text_sample, category, predicted_category, quality,
          predicted_publish, approved, admin_wallet, created_at, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            feedback_id,
            url,
            text_sample[:8000],
            corrected,
            predicted,
            quality_norm,
            predicted_publish,
            approved,
            admin_wallet,
            now,
            dict(metadata or {}),
        ),
    )
    session.execute(
        """
        INSERT INTO classifier_feedback_by_time (
          bucket, created_at, feedback_id, url, approved
        ) VALUES (%s, %s, %s, %s, %s)
        """,
        ("main", now, feedback_id, url, approved),
    )
    return str(feedback_id)


def retrain_publish_classifier(*, limit: int = 500) -> dict[str, object]:
    """Retrain RandomForest from classifier_feedback rows."""
    from datetime import UTC, datetime

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    index_rows = session.execute(
        """
        SELECT feedback_id
        FROM classifier_feedback_by_time
        WHERE bucket = %s
        LIMIT %s
        """,
        ("main", limit),
    )
    samples: list[tuple[str, str, str, int]] = []
    cat_samples: list[tuple[str, str, str]] = []
    for index_row in index_rows:
        fid = index_row.feedback_id
        if fid is None:
            continue
        row = session.execute(
            """
            SELECT url, text_sample, category, predicted_category, quality, approved
            FROM classifier_feedback
            WHERE feedback_id = %s
            """,
            (fid,),
        ).one()
        if row is None:
            continue
        text = row.text_sample or ""
        url = row.url or ""
        # Train on the category the pipeline would predict at inference time
        # (admin-corrected category is the label side, not a feature).
        cat = getattr(row, "predicted_category", None) or row.category or "generic"
        quality = (row.quality or "medium").lower()
        publish_ok = bool(row.approved) and quality in ("high", "medium")
        label = 1 if publish_ok else 0
        samples.append((text, url, cat, label))
        corrected = (row.category or "").strip().lower()
        if corrected and text:
            cat_samples.append((text, url, corrected))

    if len(samples) < 4:
        return {"status": "skipped", "reason": "insufficient_feedback", "samples": len(samples)}

    try:
        from scipy.sparse import csr_matrix, hstack
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return {"status": "skipped", "reason": "sklearn_not_installed", "samples": len(samples)}

    # TF-IDF over article text (uni+bigrams) gives real topical signal, hstacked
    # with the scalar features (relevance, length, category one-hots).
    vectorizer = TfidfVectorizer(
        max_features=4000, ngram_range=(1, 2), stop_words="english", min_df=1
    )
    texts = [_text_blob(t, u) for (t, u, _c, _l) in samples]
    x_text = vectorizer.fit_transform(texts)
    x_scalar = csr_matrix([_feature_vector(t, u, c) for (t, u, c, _l) in samples])
    x = hstack([x_text, x_scalar])
    y = [lbl for (_t, _u, _c, lbl) in samples]
    model = RandomForestClassifier(n_estimators=200, random_state=42, class_weight="balanced")
    model.fit(x, y)
    _save_model({"vectorizer": vectorizer, "model": model, "version": 2})

    cat_result = _retrain_category_model(cat_samples)

    return {
        "status": "ok",
        "samples": len(samples),
        "features": x.shape[1],
        "category_model": cat_result,
        "model_path": str(_model_path()),
        "retrained_at": datetime.now(tz=UTC).isoformat(),
    }


def _category_model_path() -> Path:
    return _model_path().parent / "category_model.pkl"


def _retrain_category_model(cat_samples: list[tuple[str, str, str]]) -> dict[str, object]:
    """Train a TF-IDF + LogisticRegression category classifier from admin
    category labels (replaces the Mistral categorizer for routing)."""
    import pickle

    distinct = {c for (_t, _u, c) in cat_samples}
    if len(cat_samples) < 6 or len(distinct) < 2:
        return {"status": "skipped", "reason": "insufficient_category_labels"}
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return {"status": "skipped", "reason": "sklearn_not_installed"}
    vec = TfidfVectorizer(max_features=4000, ngram_range=(1, 2), stop_words="english", min_df=1)
    x = vec.fit_transform([_text_blob(t, u) for (t, u, _c) in cat_samples])
    y = [c for (_t, _u, c) in cat_samples]
    clf = LogisticRegression(max_iter=1000, class_weight="balanced")
    clf.fit(x, y)
    with _category_model_path().open("wb") as fh:
        pickle.dump({"vectorizer": vec, "model": clf, "version": 1}, fh)
    return {"status": "ok", "samples": len(cat_samples), "classes": sorted(distinct)}


def predict_category_model(text: str, url: str) -> str | None:
    """Trained-model category prediction, or None when no model exists yet."""
    import pickle

    path = _category_model_path()
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            bundle = pickle.load(fh)
        x = bundle["vectorizer"].transform([_text_blob(text, url)])
        return str(bundle["model"].predict(x)[0])
    except Exception:
        return None


def service_id_for_url(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"discovered-web-{digest}"
