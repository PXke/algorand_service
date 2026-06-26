from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.modules.admin.classifier_constants import CONTENT_CATEGORIES, QUALITY_LEVELS


class ArticlePatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    summary: str | None = Field(default=None, max_length=2000)
    body: str | None = Field(default=None, max_length=200_000)


class EditorialBriefCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    body_markdown: str = Field(..., min_length=1, max_length=100_000)
    keywords: str = Field(default="", max_length=1024)
    status: str = Field(default="queued", max_length=32)


class EditorialBriefUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    body_markdown: str | None = Field(default=None, max_length=100_000)
    keywords: str | None = Field(default=None, max_length=1024)
    status: str | None = Field(default=None, max_length=32)


class OfficialChannelCreate(BaseModel):
    kind: str = Field(..., pattern="^(discord|telegram|mail_domain)$")
    channel_id: str = Field(..., min_length=1, max_length=256)
    label: str = Field(default="", max_length=256)


class ClassifierFeedbackCreate(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    text_sample: str = Field(default="", max_length=8000)
    category: str = Field(default="generic", max_length=64)
    predicted_category: str | None = Field(default=None, max_length=64)
    quality: str = Field(default="medium", max_length=32)
    # Multiple categories/keywords for the article (first is primary).
    categories: list[str] = Field(default_factory=list)
    # Source verdict, separate from the article verdict: is the SOURCE worth
    # watching? Rejecting a low-quality article keeps a good source alive.
    source_relevant: bool = True
    predicted_publish: bool = False
    approved: bool
    # Training mode: record the label + grade dimensions (both models learn) but
    # do NOT publish an accepted article to the live feed — for the bootstrap
    # labelling sprint where the first articles are low-quality.
    training_only: bool = False
    # Human-corrected per-dimension scores (0-10), only the ones the reviewer
    # disagreed with. Become ground truth for the grader + dimension scorers.
    corrected_scores: dict[str, float] = Field(default_factory=dict)
    # Gatekeeper validation anchor: when `anchor` is set, this graded item joins
    # the immutable anchor set with explicit factuality/tone failure tags + error
    # types, used only to validate the LLM annotator (never to train).
    anchor: bool = False
    factuality_fail: bool = False
    tone_fail: bool = False
    error_types: list[str] = Field(default_factory=list)
    review_id: str | None = Field(default=None, max_length=64)
    article_id: str | None = Field(default=None, max_length=64)


    @field_validator("category", "predicted_category")
    @classmethod
    def validate_category(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in CONTENT_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(CONTENT_CATEGORIES)}")
        return normalized

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in QUALITY_LEVELS:
            raise ValueError(f"quality must be one of: {', '.join(QUALITY_LEVELS)}")
        return normalized


class GatekeeperAnchorCreate(BaseModel):
    """Tag an already-published article into the gatekeeper validation anchor set."""

    article_id: str = Field(..., min_length=1, max_length=64)
    factuality_fail: bool = False
    tone_fail: bool = False
    error_types: list[str] = Field(default_factory=list)


class SourceUpsertRequest(BaseModel):
    service_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name: str = Field(..., min_length=1, max_length=256)
    scrape_url: str = Field(..., min_length=1, max_length=2048)
    match_kind: str = Field(default="domain", max_length=64)
    match_value: str = Field(default="", max_length=512)
    enabled: bool = True


class ScraperRunRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=64)


class DomainSetRequest(BaseModel):
    domain: str = Field(..., min_length=3, max_length=256)
    is_relevant: bool
