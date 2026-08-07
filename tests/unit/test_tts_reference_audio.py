import io
import wave

from app.routers.tts import normalize_reference_wavs


def make_wav(seconds: int = 1, channels: int = 2, rate: int = 44100) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\x00\x00" * channels * rate * seconds)
    return stream.getvalue()


def test_reference_wavs_are_normalized_and_trimmed():
    result = normalize_reference_wavs([make_wav(15), make_wav(10)])

    with wave.open(io.BytesIO(result), "rb") as reference:
        assert reference.getnchannels() == 1
        assert reference.getsampwidth() == 2
        assert reference.getframerate() == 24000
        assert reference.getnframes() == 24000 * 25
