from abc import ABC, abstractmethod


class ASRError(RuntimeError):
    pass


class ASRUpstreamError(ASRError):
    pass


class ASRConfigurationError(ASRError):
    pass


class ASREmptyResultError(ASRError):
    pass


class ASRProvider(ABC):
    @abstractmethod
    async def transcribe(self, filename: str, content_type: str, audio: bytes) -> str:
        raise NotImplementedError
