import base64
import io
import json
import wave
from pathlib import Path
from urllib.error import URLError

import pytest

from voice.tts.local_worker import LocalTTS, TTSGenerationError


def wav_audio(seconds: float = 2.0, rate: int = 24000) -> bytes:
    stream = io.BytesIO()
    frames = b"\x00\x00" * int(rate * seconds)
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)
    return stream.getvalue()


def audio_payload(seconds: float = 2.0) -> bytes:
    return json.dumps({"success": True, "audio": base64.b64encode(wav_audio(seconds)).decode()}).encode()


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_local_tts_posts_to_service_and_writes_wav(tmp_path: Path):
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
        return Response(audio_payload())

    LocalTTS(runtime, model_dir, opener=opener).synthesize("hello", output, reference)

    assert requests[-1].full_url.endswith("/tts")
    assert json.loads(requests[-1].data) == {"text": "hello", "ref_audio": str(reference), "max_tokens": 160}
    assert output.read_bytes() == wav_audio()


def test_local_tts_rejects_invalid_service_response(tmp_path: Path):
    runtime = tmp_path / "Qwen3_TTS_Lunar.exe"
    runtime.write_bytes(b"exe")

    def opener(request, timeout):
        if request.full_url.endswith("/health"):
            return Response(b'{"status":"ok"}')
        return Response(b'{"success": false, "error": "failed"}')

    with pytest.raises(TTSGenerationError, match="failed"):
        LocalTTS(runtime, tmp_path, opener=opener).synthesize("hello", tmp_path / "out.wav")


def test_local_tts_retries_synthesis_after_connection_failure(tmp_path: Path):
    runtime = tmp_path / "Qwen3_TTS_Lunar.exe"
    model_dir = tmp_path / "models" / "Qwen3-TTS"
    output = tmp_path / "reply.wav"
    runtime.write_bytes(b"exe")
    model_dir.mkdir(parents=True)
    tts_calls = {"n": 0}

    def opener(request, timeout):
        if request.full_url.endswith("/health"):
            return Response(b'{"status":"ok"}')
        tts_calls["n"] += 1
        if tts_calls["n"] == 1:
            raise URLError("connection refused")
        return Response(audio_payload())

    LocalTTS(runtime, model_dir, opener=opener).synthesize("hello", output)

    assert tts_calls["n"] == 2
    assert output.read_bytes() == wav_audio()


def test_local_tts_instances_do_not_share_a_fixed_port(tmp_path: Path):
    first = LocalTTS(tmp_path / "one.exe", tmp_path)
    second = LocalTTS(tmp_path / "two.exe", tmp_path)

    assert first.port != second.port


def test_split_chunks_by_sentence_boundaries():
    chunks = LocalTTS.split_chunks("第一句。第二句！第三句？", max_chars=4)
    assert chunks == ["第一句。", "第二句！", "第三句？"]


def test_split_chunks_hard_cuts_without_punctuation():
    chunks = LocalTTS.split_chunks("一二三四五六七八九", max_chars=4)
    assert chunks == ["一二三四", "五六七八", "九"]


def test_split_chunks_short_text_stays_whole():
    assert LocalTTS.split_chunks("你好世界。", max_chars=150) == ["你好世界。"]


def test_stream_segments_splits_sentences():
    segs = LocalTTS.stream_segments("第一句。第二句！第三句？", max_chars=12)
    assert segs == ["第一句。第二句！第三句？"]
    segs = LocalTTS.stream_segments("第一句。第二句！第三句？", max_chars=6)
    assert segs == ["第一句。", "第二句！", "第三句？"]


def test_stream_segments_hard_cuts_long_sentence():
    segs = LocalTTS.stream_segments("一二三四五六七八九十", max_chars=4)
    assert segs == ["一二三四", "五六七八", "九十"]


def test_stream_segments_empty_text_returns_empty():
    assert LocalTTS.stream_segments("   ") == []


def test_short_output_triggers_restart_and_retry(tmp_path: Path):
    runtime = tmp_path / "Qwen3_TTS_Lunar.exe"
    model_dir = tmp_path / "models" / "Qwen3-TTS"
    output = tmp_path / "reply.wav"
    runtime.write_bytes(b"exe")
    model_dir.mkdir(parents=True)
    tts_calls = {"n": 0}

    def opener(request, timeout):
        if request.full_url.endswith("/health"):
            return Response(b'{"status":"ok"}')
        tts_calls["n"] += 1
        if tts_calls["n"] == 1:
            return Response(audio_payload(seconds=0.1))
        return Response(audio_payload(seconds=2.0))

    LocalTTS(runtime, model_dir, opener=opener).synthesize("hello", output)

    assert tts_calls["n"] == 2
    assert output.read_bytes() == wav_audio()


def test_long_text_is_split_into_multiple_requests_and_merged(tmp_path: Path):
    runtime = tmp_path / "Qwen3_TTS_Lunar.exe"
    model_dir = tmp_path / "models" / "Qwen3-TTS"
    output = tmp_path / "reply.wav"
    runtime.write_bytes(b"exe")
    model_dir.mkdir(parents=True)
    requests = []

    def opener(request, timeout):
        if request.full_url.endswith("/health"):
            return Response(b'{"status":"ok"}')
        requests.append(request)
        return Response(audio_payload(seconds=2.0))

    worker = LocalTTS(runtime, model_dir, opener=opener)
    worker.chunk_chars = 10
    worker.synthesize("你好，这是一段用于测试切分的文本。", output)

    assert len(requests) == 2
    with wave.open(str(output), "rb") as source:
        assert source.getframerate() == 24000
        assert source.getnframes() == 2 * 24000 * 2


def test_consistently_short_output_falls_back_after_retry(tmp_path: Path):
    runtime = tmp_path / "Qwen3_TTS_Lunar.exe"
    model_dir = tmp_path / "models" / "Qwen3-TTS"
    output = tmp_path / "out.wav"
    runtime.write_bytes(b"exe")
    model_dir.mkdir(parents=True)
    tts_calls = {"n": 0}

    def opener(request, timeout):
        if request.full_url.endswith("/health"):
            return Response(b'{"status":"ok"}')
        tts_calls["n"] += 1
        return Response(audio_payload(seconds=0.1))

    LocalTTS(runtime, model_dir, opener=opener).synthesize("hello", output)

    assert tts_calls["n"] == 2
    assert output.read_bytes() == wav_audio(seconds=0.1)


def test_runaway_cap_length_output_triggers_retry(tmp_path: Path):
    runtime = tmp_path / "Qwen3_TTS_Lunar.exe"
    model_dir = tmp_path / "models" / "Qwen3-TTS"
    output = tmp_path / "out.wav"
    runtime.write_bytes(b"exe")
    model_dir.mkdir(parents=True)
    tts_calls = {"n": 0}

    def opener(request, timeout):
        if request.full_url.endswith("/health"):
            return Response(b'{"status":"ok"}')
        tts_calls["n"] += 1
        if tts_calls["n"] == 1:
            # 15.36s ≈ 192/12.5，模拟"短文本却说满上限"
            return Response(audio_payload(seconds=15.36))
        return Response(audio_payload(seconds=2.0))

    LocalTTS(runtime, model_dir, opener=opener).synthesize("hello", output)

    assert tts_calls["n"] == 2
    assert output.read_bytes() == wav_audio()
