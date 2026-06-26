from __future__ import annotations

from pydantic import BaseModel, Field


class IngestSignalRequest(BaseModel):
    """Push official announcements when bots cannot join Discord/Telegram."""

    service_id: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=256)
    page_text: str = Field(..., min_length=1, max_length=100_000)
    page_title: str = Field(default="Announcement", max_length=512)
    source_url: str = Field(default="", max_length=2048)
    source_kind: str = Field(default="push", max_length=32)
    match_kind: str = Field(default="push", max_length=64)
    match_value: str = Field(default="", max_length=512)
    mail_from: str = Field(default="", max_length=512)
