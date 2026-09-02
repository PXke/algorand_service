"""Re-export shim — definitions live in app/schemas.py (msgspec.Struct)."""

from app.schemas import (  # noqa: F401
    AdminSourceCreateRequest,
    AdminSourceCreateResponse,
    AdminSourceItem,
    ArticleDraftRequest,
    ArticlePatchRequest,
    ClassifierFeedbackCreate,
    DomainSetRequest,
    EditorialBriefCreate,
    GlossaryUpsertRequest,
    ScraperRunRequest,
    ServiceMergeRequest,
    ShareLinkCreateRequest,
    SourceUpsertRequest,
)
