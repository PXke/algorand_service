from __future__ import annotations

import pytest

from app.modules.ai.mistral_client import MistralError
from app.modules.ai.voxtral_client import transcribe_audio


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"fake mp3 bytes")
    return str(path)


def test_transcribe_audio_returns_text(monkeypatch, audio_file) -> None:
    import app.modules.ai.voxtral_client as voxtral_module

    monkeypatch.setattr(voxtral_module, "MISTRAL_API_KEY", "test-key")

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"text": "  hello world  "}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, headers=None, files=None, data=None):
            assert "audio/transcriptions" in url
            assert headers["Authorization"] == "Bearer test-key"
            assert data["model"] == voxtral_module.MISTRAL_VOXTRAL_MODEL
            assert "file" in files
            return FakeResponse()

    monkeypatch.setattr(voxtral_module.httpx, "Client", FakeClient)

    result = transcribe_audio(audio_file)
    assert result == "hello world"


def test_transcribe_audio_raises_on_missing_key(monkeypatch, audio_file) -> None:
    import app.modules.ai.voxtral_client as voxtral_module

    monkeypatch.setattr(voxtral_module, "MISTRAL_API_KEY", "")

    with pytest.raises(MistralError, match="MISTRAL_API_KEY"):
        transcribe_audio(audio_file)


def test_transcribe_audio_raises_on_http_error(monkeypatch, audio_file) -> None:
    import app.modules.ai.voxtral_client as voxtral_module

    monkeypatch.setattr(voxtral_module, "MISTRAL_API_KEY", "test-key")

    class FakeResponse:
        status_code = 500
        text = "server error"

        def json(self) -> dict:
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, headers=None, files=None, data=None):
            return FakeResponse()

    monkeypatch.setattr(voxtral_module.httpx, "Client", FakeClient)

    with pytest.raises(MistralError, match="500"):
        transcribe_audio(audio_file)
