from pydantic import BaseModel, Field


class CreateSuggestionRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=5000)
    submission_txid: str = Field(min_length=52, max_length=52)


class SuggestionResponse(BaseModel):
    suggestion_id: str
    wallet_address: str
    title: str
    body: str
    submission_txid: str
    status: str
    created_at_epoch: int
    upvote_count: int = 0


class SuggestionConfigResponse(BaseModel):
    treasury_address: str
    min_microalgos: int
    min_algo_display: str


class SuggestionListResponse(BaseModel):
    items: list[SuggestionResponse]


class UpvoteRequest(BaseModel):
    signature_b64: str = Field(min_length=16, max_length=2048)


class UpvoteResponse(BaseModel):
    suggestion_id: str
    upvote_count: int
