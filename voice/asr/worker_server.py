import os
import tempfile
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from qwen_asr import Qwen3ASRModel


bundled_model = Path(r"D:\Qwen3_ASR\models\Qwen\Qwen3-ASR-0.6B")
MODEL_ID = os.getenv("PERSONALIVE_ASR_MODEL") or (
    str(bundled_model) if bundled_model.is_dir() else "Qwen/Qwen3-ASR-0.6B"
)
app = FastAPI(title="PersonaLive Local ASR")
model = None


@app.on_event("startup")
def load_model() -> None:
    global model
    model = Qwen3ASRModel.from_pretrained(
        MODEL_ID,
        dtype=torch.bfloat16,
        device_map="cuda:0",
        max_inference_batch_size=1,
        max_new_tokens=256,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ready" if model is not None else "loading"}


@app.post("/transcribe")
async def transcribe(request: Request, x_audio_filename: str = Header(default="recording.webm")) -> dict[str, str]:
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=422, detail="Audio is empty")
    suffix = Path(x_audio_filename).suffix or ".webm"
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(audio)
            path = temporary.name
        result = model.transcribe(audio=path, language=None)[0]
        return {"text": str(result.text).strip()}
    finally:
        if path:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
