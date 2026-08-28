"""Mistral Voxtral audio transcription — same account as the article writer, a different endpoint (multipart /audio/transcriptions, not JSON chat/completions) so it isn't shoehorned into MistralProvider._post."""

from __future__ import annotations

from pathlib import Path

from app.core.config import (
    MISTRAL_API_BASE,
    MISTRAL_API_KEY,
    MISTRAL_VOXTRAL_MODEL,
    MISTRAL_VOXTRAL_TIMEOUT,
)
from app.modules.ai.llm_provider import LLMError


def transcribe_audio(audio_path: str, *, timeout: float | None = None) -> str:
    """Upload a local audio file to Mistral's Voxtral transcription endpoint and return the transcript text. Raises LLMError on failure — this is an inner call, not a never-raises boundary; callers that need best-effort behavior (e.g. the YouTube transcript pipeline) must catch it themselves."""
    from app.core.http_client import get_http_client

    if not MISTRAL_API_KEY:
        raise LLMError("MISTRAL_API_KEY not configured")

    client = get_http_client(timeout=timeout or MISTRAL_VOXTRAL_TIMEOUT)
    with Path(audio_path).open("rb") as f:
        resp = client.post(
            f"{MISTRAL_API_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {MISTRAL_API_KEY}"},
            files={"file": (Path(audio_path).name, f, "audio/mpeg")},
            data={"model": MISTRAL_VOXTRAL_MODEL},
        )
    if resp.status_code >= 400:
        raise LLMError(f"Voxtral transcription failed: {resp.status_code} {resp.text[:300]}")
    return resp.json().get("text", "").strip()
