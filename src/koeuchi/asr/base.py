"""ASR backend abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

import numpy as np

ProgressCallback = Callable[[str], None]


class ASRBackend(ABC):
    @abstractmethod
    def transcribe(
        self, audio: np.ndarray, on_progress: ProgressCallback | None = None
    ) -> str:
        """Transcribe 16kHz mono float32 audio. When given, on_progress is
        called with the partial text so far; backends may ignore it."""

    def warmup(self) -> None:
        """Optional: absorb first-inference latency at startup."""
