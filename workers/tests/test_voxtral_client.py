"""Voxtral audio transcription success and failure paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import pytest

from app.modules.ai.mistral_client import MistralError
from app.modules.ai.voxtral_client import transcribe_audio


@pytest.fixture
def audio_file(tmp_path: Path) -> str:
    """Write a fake mp3 file and return its path."""
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"fake mp3 bytes")
    return str(path)


def test_transcribe_audio_returns_text(monkeypatch: pytest.MonkeyPatch, audio_file: str) -> None:
    """Posts the audio file to the Voxtral endpoint and returns the trimmed transcribed text."""
    import app.modules.ai.voxtral_client as voxtral_module

    monkeypatch.setattr(voxtral_module, "MISTRAL_API_KEY", "test-key")

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"text": "  hello world  "}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(
            self,
            url: str,
            headers: dict | None = None,
            files: dict | None = None,
            data: dict | None = None,
        ) -> Any:  # noqa: ANN401 -- test double / fake response
            assert "audio/transcriptions" in url
            assert headers["Authorization"] == "Bearer test-key"
            assert data["model"] == voxtral_module.MISTRAL_VOXTRAL_MODEL
            assert "file" in files
            return FakeResponse()

    monkeypatch.setattr(voxtral_module.httpx, "Client", FakeClient)

    result = transcribe_audio(audio_file)
    assert result == "hello world"


def test_transcribe_audio_raises_on_missing_key(
    monkeypatch: pytest.MonkeyPatch, audio_file: str
) -> None:
    """Raises MistralError when MISTRAL_API_KEY is unset instead of calling out."""
    import app.modules.ai.voxtral_client as voxtral_module

    monkeypatch.setattr(voxtral_module, "MISTRAL_API_KEY", "")

    with pytest.raises(MistralError, match="MISTRAL_API_KEY"):
        transcribe_audio(audio_file)


def test_transcribe_audio_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch, audio_file: str
) -> None:
    """Raises MistralError carrying the status code when the API responds with an HTTP error."""
    import app.modules.ai.voxtral_client as voxtral_module

    monkeypatch.setattr(voxtral_module, "MISTRAL_API_KEY", "test-key")

    class FakeResponse:
        status_code = 500
        text = "server error"

        def json(self) -> dict:
            return {}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(
            self,
            _url: str,
            # Unused here, but the names must match the real callee's keyword args.
            headers: dict | None = None,  # noqa: ARG002
            files: dict | None = None,  # noqa: ARG002
            data: dict | None = None,  # noqa: ARG002
        ) -> Any:  # noqa: ANN401 -- test double / fake response
            return FakeResponse()

    monkeypatch.setattr(voxtral_module.httpx, "Client", FakeClient)

    with pytest.raises(MistralError, match="500"):
        transcribe_audio(audio_file)
