from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import numpy as np

VADEventKind = Literal["speech_start", "speech_stop"]


@dataclass(frozen=True)
class VADEvent:
    """One speech boundary detected on the stream.

    sample_index is an absolute position (samples at 16 kHz) counted from the
    last reset(), shared with the caller's own audio accounting.
    """

    kind: VADEventKind
    sample_index: int


class VAD(ABC):
    """Streaming voice activity detector.

    Call process() repeatedly with mono PCM chunks; events carry absolute
    sample indices so the caller can slice its own audio buffer precisely.
    """

    sample_rate = 16000

    @abstractmethod
    def reset(self) -> None:
        """Start a fresh stream (also resets internal model state)."""

    @abstractmethod
    def process(self, pcm: np.ndarray) -> list[VADEvent]:
        """Feed one PCM chunk; returns boundary events detected so far."""
