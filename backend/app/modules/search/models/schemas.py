from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    article_id: str
    title: str
    summary: str
    service_id: str | None = None
    published_at_epoch: int | None = None
    score: float | None = None


class SearchResponse(BaseModel):
    query: str
    engine: str
    items: list[SearchHit] = Field(default_factory=list)
