import httpx

from settings import Settings
from voice.asr.base import ASRConfigurationError, ASREmptyResultError, ASRProvider, ASRUpstreamError


class OpenAICompatibleASR(ASRProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        language: str = "",
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = f"{base_url.rstrip('/')}/audio/transcriptions"
        self.api_key = api_key
        self.model = model
        self.language = language.strip()
        self.client = client

    async def transcribe(self, filename: str, content_type: str, audio: bytes) -> str:
        files = {"file": (filename, audio, content_type)}
        data = {"model": self.model}
        if self.language:
            data["language"] = self.language
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            if self.client is not None:
                response = await self.client.post(self.endpoint, headers=headers, data=data, files=files)
            else:
                async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
                    response = await client.post(self.endpoint, headers=headers, data=data, files=files)
        except httpx.HTTPError as exc:
            raise ASRUpstreamError("ASR service request failed") from exc
        if not response.is_success:
            raise ASRUpstreamError(f"ASR service returned HTTP {response.status_code}")
        try:
            text = str(response.json().get("text") or "").strip()
        except (ValueError, AttributeError) as exc:
            raise ASRUpstreamError("ASR service returned an invalid response") from exc
        if not text:
            raise ASREmptyResultError("No speech was recognized")
        return text


def build_asr_provider(settings: Settings) -> ASRProvider:
    if settings.asr_provider == "off":
        raise ASRConfigurationError("ASR is disabled")
    if not settings.asr_api_key or not settings.asr_base_url or not settings.asr_model:
        raise ASRConfigurationError("ASR configuration is incomplete")
    return OpenAICompatibleASR(
        base_url=settings.asr_base_url,
        api_key=settings.asr_api_key,
        model=settings.asr_model,
        language=settings.asr_language,
    )
