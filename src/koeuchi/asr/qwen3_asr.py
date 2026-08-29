"""Qwen3-ASR backend via mlx-audio."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .base import ASRBackend, ProgressCallback

# MLX bf16 conversion of neosophie/Qwen3-ASR-1.7B-JA; pinned to a commit hash
# as a supply-chain measure (verified against a local conversion, see README).
DEFAULT_REPO = "ph0ryn/Qwen3-ASR-1.7B-JA-MLX-bf16"
DEFAULT_REVISION = "47090c8ba5d57a6294d527adfd76b321c732690f"

SAMPLE_RATE = 16000
_PROGRESS_INTERVAL = 0.2
# Speech decodes at roughly 8 tokens/s of audio for Japanese (the densest
# common case; English is closer to 4), so this cap keeps a >=2x margin while
# stopping runaway repetition loops that otherwise generate until the library
# default of 8192 tokens (minutes).
_MAX_TOKENS_PER_SEC = 16.0
_MAX_TOKENS_BASE = 128


def _prepare_model(model: str, revision: str | None) -> str:
    """Materialize the model and return its local path."""
    if model.startswith(("~", "/", ".")):
        path = Path(model).expanduser()
        if not path.is_dir():
            raise FileNotFoundError(f"モデルディレクトリがありません: {path}")
        return str(path)
    from huggingface_hub import snapshot_download

    if revision is None and model == DEFAULT_REPO:
        revision = DEFAULT_REVISION
    return snapshot_download(model, revision=revision)


class Qwen3ASRBackend(ASRBackend):
    def __init__(
        self,
        model: str,
        revision: str | None = None,
        language: str | None = None,
    ):
        self._language = language or None
        self._path = _prepare_model(model, revision)
        self._model = None

    def _load(self):
        # Deferred to warmup() so load time can be measured and reported.
        if self._model is None:
            from mlx_audio.stt.utils import load_model

            self._model = load_model(self._path)
        return self._model

    def transcribe(
        self, audio: np.ndarray, on_progress: ProgressCallback | None = None
    ) -> str:
        model = self._load()
        max_tokens = _MAX_TOKENS_BASE + int(len(audio) / SAMPLE_RATE * _MAX_TOKENS_PER_SEC)
        if on_progress is None:
            return model.generate(
                audio, language=self._language, max_tokens=max_tokens
            ).text.strip()
        # mlx-audio's stream=True decodes tokens one at a time, which yields
        # U+FFFD when a multibyte character splits across tokens; collect the
        # ids and re-decode from the start instead.
        from mlx_lm.sample_utils import make_sampler

        ids: list[int] = []
        last_emit = 0.0
        for token, _ in model.stream_generate(
            audio, language=self._language, sampler=make_sampler(0.0), max_tokens=max_tokens
        ):
            ids.append(int(token))
            now = time.monotonic()
            if now - last_emit >= _PROGRESS_INTERVAL:
                last_emit = now
                on_progress(model._tokenizer.decode(ids, skip_special_tokens=True))
        text = model._tokenizer.decode(ids, skip_special_tokens=True)
        # Strip the "language Xxx<asr_text>" prefix emitted under auto-detect.
        _, text = model.extract_language(text)
        return text.strip()

    def warmup(self) -> None:
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
