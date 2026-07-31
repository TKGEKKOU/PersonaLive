"""隔离环境中的 Sentence Transformers JSON Lines 工作进程。"""

import argparse
import json
import sys
from pathlib import Path


def load_model(model_dir: Path, requested_device: str):
    import torch
    from sentence_transformers import SentenceTransformer

    candidates = [requested_device]
    if requested_device == "auto":
        candidates = ["cuda", "cpu"] if torch.cuda.is_available() else ["cpu"]
    last_error = None
    for device in candidates:
        try:
            # 自定义模型不得执行仓库代码，避免零代码下载变成任意代码执行入口。
            model = SentenceTransformer(str(model_dir), device=device, trust_remote_code=False)
            return model, device
        except Exception as exc:
            last_error = exc
            if device == "cuda" and requested_device == "auto":
                torch.cuda.empty_cache()
                continue
            raise
    raise RuntimeError(str(last_error or "模型加载失败"))


def encode(model, texts: list[str]) -> list[list[float]]:
    values = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
    return values.tolist()


def probe(model_dir: Path, device: str) -> dict:
    try:
        model, actual_device = load_model(model_dir, device)
        vector = encode(model, ["维度探测"])[0]
        return {"ok": True, "dimensions": len(vector), "actual_device": actual_device}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def serve(model_dir: Path, device: str) -> None:
    model, actual_device = load_model(model_dir, device)
    print(json.dumps({"ok": True, "event": "ready", "actual_device": actual_device}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            texts = request.get("texts") or []
            if request.get("operation") not in {"embed_documents", "embed_query"} or not isinstance(texts, list):
                raise ValueError("无效的 Embedding 请求")
            print(json.dumps({"ok": True, "vectors": encode(model, [str(text) for text in texts])}), flush=True)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("device", choices=["auto", "cuda", "cpu"])
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(probe(args.model_dir, args.device), ensure_ascii=False))
    else:
        serve(args.model_dir, args.device)
