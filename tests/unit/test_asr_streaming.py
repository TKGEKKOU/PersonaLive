import asyncio

import numpy as np
import pytest

from voice.asr.base import ASREmptyResultError
from voice.asr.streaming import StreamSession


def test_stream_session_accumulates_and_transcribes():
    seen = []

    async def infer(pcm):
        seen.append(pcm)
        return "Chinese", "你好 world"

    async def run():
        session = StreamSession(infer)
        await session.feed(np.zeros(8000, dtype=np.int16))
        await session.feed(np.full(8000, 100, dtype=np.int16))
        result = await session.transcribe()
        return session.audio, result

    audio, result = asyncio.run(run())
    assert audio.shape[0] == 16000
    assert seen[-1].shape[0] == 16000
    assert result == ("Chinese", "你好 world")


def test_stream_session_empty_raises():
    async def infer(pcm):
        raise AssertionError("infer must not run for empty audio")

    async def run():
        session = StreamSession(infer)
        return await session.transcribe()

    with pytest.raises(ASREmptyResultError):
        asyncio.run(run())


def test_stream_session_too_long_raises():
    async def infer(pcm):
        return "Chinese", ""

    async def run():
        session = StreamSession(infer, max_seconds=1)
        await session.feed(np.zeros(16000, dtype=np.int16))
        await session.feed(np.zeros(16000, dtype=np.int16))

    with pytest.raises(ValueError, match="utterance_too_long"):
        asyncio.run(run())


def test_stream_session_cancel_clears_audio():
    async def infer(pcm):
        return "Chinese", ""

    async def run():
        session = StreamSession(infer)
        await session.feed(np.zeros(16000, dtype=np.int16))
        session.cancel()
        return session.audio

    assert asyncio.run(run()).shape[0] == 0
