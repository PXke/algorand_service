from pydantic import BaseModel


class ArticleFeedItem(BaseModel):
    article_id: str
    service_id: str
    title: str
    summary: str
    published_at_epoch: int
    trigger_txid: str | None = None
    trigger_round: int | None = None
    tags: list[str] = []
    trigger_kind: str = "editorial"
    image_url: str | None = None
    source_url: str | None = None


class ArticleDetail(BaseModel):
    article_id: str
    service_id: str
    title: str
    summary: str
    body: str
    published_at_epoch: int
    trigger_txid: str | None = None
    trigger_round: int | None = None
    source_url: str | None = None
    tags: list[str] = []
    trigger_kind: str = "editorial"
    views: int = 0
    image_url: str | None = None


class NewsFeedResponse(BaseModel):
    items: list[ArticleFeedItem]


class ServiceEventItem(BaseModel):
    service_id: str
    event_id: str
    txid: str
    round: int
    occurred_at_epoch: int
