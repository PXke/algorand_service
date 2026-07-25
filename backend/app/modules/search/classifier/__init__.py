"""Keyword-based relevance scoring for crawled pages."""

from app.modules.search.classifier.score import ClassifierResult, score_page

__all__ = ["ClassifierResult", "score_page"]
