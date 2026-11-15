from __future__ import annotations

import threading

import open_clip
import torch

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"


def pick_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class ClipTextEncoder:
    _lock = threading.Lock()

    def __init__(self, device: str | None = None):
        self.device = device or pick_device()
        model, _, _ = open_clip.create_model_and_transforms(
            MODEL_NAME, pretrained=PRETRAINED
        )
        self.model = model.to(self.device).eval()
        self.tokenizer = open_clip.get_tokenizer(MODEL_NAME)

    @torch.inference_mode()
    def encode(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            tokens = self.tokenizer(texts).to(self.device)
            features = self.model.encode_text(tokens)
            features = features / features.norm(p=2, dim=-1, keepdim=True)
            return features.cpu().numpy().tolist()

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]
