"""Re-export shim — definitions live in app/schemas.py (msgspec.Struct)."""

from app.schemas import (  # noqa: F401
    ArticlePatchRequest,
    ClassifierFeedbackCreate,
    DomainSetRequest,
    EditorialBriefCreate,
    GatekeeperAnchorCreate,
    GlossaryUpsertRequest,
    OfficialChannelCreate,
    ScraperRunRequest,
    ServiceMergeRequest,
    SourceUpsertRequest,
    ToolSuggestionResolveRequest,
)
