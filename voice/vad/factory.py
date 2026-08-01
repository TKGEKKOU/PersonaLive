from __future__ import annotations

from voice.vad.base import VAD
from voice.vad.energy import EnergyVAD


def detect_vad_provider() -> str:
    """Report which VAD implementation is actually usable."""

    try:
        import silero_vad  # noqa: F401
    except Exception:
        return "energy"
    return "silero"


def build_vad() -> VAD:
    if detect_vad_provider() == "silero":
        try:
            from voice.vad.silero import SileroVAD

            return SileroVAD()
        except Exception:
            # Silero present but unusable (e.g. packaged build missing the
            # bundled model file): degrade to the energy fallback instead of
            # breaking the voice stream.
            pass
    return EnergyVAD()
