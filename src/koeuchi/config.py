"""Configuration, overridable via ~/.config/koeuchi/config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .asr.qwen3_asr import DEFAULT_REPO

CONFIG_PATH = Path.home() / ".config" / "koeuchi" / "config.toml"


@dataclass
class Config:
    # Push-to-talk key: an attribute name of pynput.keyboard.Key.
    hotkey: str = "alt_r"
    # HuggingFace repo name or local path. The default repo is pinned to a
    # commit hash (DEFAULT_REVISION in asr/qwen3_asr.py).
    model: str = DEFAULT_REPO
    model_revision: str | None = None
    # Auto-send Cmd+V after copying. Requires Accessibility permission for the
    # launching app; without it the keystroke is silently dropped.
    auto_paste: bool = False
    min_duration: float = 0.3
    sample_rate: int = 16000  # Qwen3-ASR expects 16kHz
    sounds: bool = True
    overlay: bool = True
    menu_bar: bool = True
    # None means model auto-detect, which can corrupt output when it guesses
    # wrong (e.g. a "language Sao." prefix), so pin it by default.
    language: str | None = "Japanese"
    # Deterministic replacements {misrecognized: correct} applied to results.
    replacements: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            return cls()
        with path.open("rb") as f:
            data = tomllib.load(f)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
