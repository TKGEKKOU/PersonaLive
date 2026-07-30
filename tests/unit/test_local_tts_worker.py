import base64
import json
from pathlib import Path

import pytest

from voice.tts.local_worker import LocalTTS, TTSGenerationError


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_local_tts_posts_to_lunar_service_and_writes_wav(tmp_path: Path):
    runtime = tmp_path / "Qwen3_TTS_Lunar.exe"
    model_dir = tmp_path / "models" / "Qwen3-TTS"
    reference = tmp_path / "voice.wav"
    output = tmp_path / "reply.wav"
    runtime.write_bytes(b"exe")
    model_dir.mkdir(parents=True)
    reference.write_bytes(b"wav")
    requests = []

    def opener(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/health"):
            return Response(b'{"status":"ok"}')
        return Response(json.dumps({"success": True, "audio": base64.b64encode(b"RIFFaudio").decode()}).encode())

    LocalTTS(runtime, model_dir, opener=opener).synthesize("hello", output, reference)

    assert requests[-1].full_url.endswith("/tts")
    assert json.loads(requests[-1].data) == {"text": "hello", "ref_audio": str(reference)}
    assert output.read_bytes() == b"RIFFaudio"


def test_local_tts_rejects_invalid_service_response(tmp_path: Path):
    runtime = tmp_path / "Qwen3_TTS_Lunar.exe"
    runtime.write_bytes(b"exe")

    def opener(request, timeout):
        if request.full_url.endswith("/health"):
            return Response(b'{"status":"ok"}')
        return Response(b'{"success": false, "error": "failed"}')

    with pytest.raises(TTSGenerationError, match="failed"):
        LocalTTS(runtime, tmp_path, opener=opener).synthesize("hello", tmp_path / "out.wav")
