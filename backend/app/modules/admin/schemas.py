"""Re-export shim — definitions live in app/schemas.py (msgspec.Struct)."""

from app.schemas import (  # noqa: F401
    ArticleDraftRequest,
    ArticlePatchRequest,
    ClassifierFeedbackCreate,
    DomainSetRequest,
    EditorialBriefCreate,
    GatekeeperAnchorCreate,
    GlossaryUpsertRequest,
    ScraperRunRequest,
    ServiceMergeRequest,
    ShareLinkCreateRequest,
    SourceUpsertRequest,
)
