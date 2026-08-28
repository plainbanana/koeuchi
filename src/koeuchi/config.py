"""Configuration, overridable via ~/.config/koeuchi/config.toml."""

from __future__ import annotations

import argparse
import textwrap
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from .asr.qwen3_asr import DEFAULT_REPO

CONFIG_PATH = Path.home() / ".config" / "koeuchi" / "config.toml"

_COMMENT_COL = 29
_HELP_WRAP = 47


def _opt(default=None, help_text: str = "", *, show_default: bool = True, factory=None):
    kwargs = {"default_factory": factory} if factory is not None else {"default": default}
    return field(metadata={"help": help_text, "show_default": show_default}, **kwargs)


@dataclass
class Config:
    hotkey: str = _opt(
        "alt_r",
        "push-to-talk key; an attribute name of pynput.keyboard.Key "
        "(e.g. alt_r, cmd_r, f13)",
    )
    model: str = _opt(
        DEFAULT_REPO,
        "ASR model to use: HuggingFace repo name or a local path",
        show_default=False,
    )
    model_revision: str | None = _opt(
        None,
        "model git revision; the default model is already pinned to a commit",
    )
    auto_paste: bool = _opt(
        False,
        "send Cmd+V after copying the result (needs Accessibility permission "
        "for the launching app)",
    )
    min_duration: float = _opt(
        0.3, "discard recordings shorter than this (seconds)"
    )
    sample_rate: int = _opt(
        16000,
        "recording sample rate (Hz); must match what the ASR model expects, "
        "rarely needs changing",
    )
    sounds: bool = _opt(True, "stop/done/error sound effects")
    overlay: bool = _opt(True, "recording pill UI at the bottom of the screen")
    menu_bar: bool = _opt(True, "menu bar status icon")
    language: str | None = _opt(
        "Japanese",
        "recognition language; unset it for auto-detect (risks misdetection)",
    )
    bias_text: str | None = _opt(
        None,
        "free-form text injected into the ASR system prompt to bias "
        "recognition toward your vocabulary (names, jargon, phrases)",
    )
    replacements: dict[str, str] = _opt(
        help_text="deterministic fixes for common misrecognitions, "
        'e.g. "クロードコード" = "Claude Code" (wrong = right)',
        factory=dict,
    )

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            return cls()
        with path.open("rb") as f:
            data = tomllib.load(f)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def _toml_literal(value) -> str:
    if value is None:
        return "(unset)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


_CLI_TYPES = {"str": str, "int": int, "float": float}


def _replace_pair(value: str) -> tuple[str, str]:
    wrong, sep, right = value.partition("=")
    if not sep or not wrong:
        raise argparse.ArgumentTypeError(f"expected WRONG=RIGHT, got {value!r}")
    return wrong, right


def add_cli_options(parser: argparse.ArgumentParser) -> None:
    """One option per config key, generated from the dataclass."""
    for f in fields(Config):
        if f.name == "replacements":
            parser.add_argument(
                "--replace",
                action="append",
                type=_replace_pair,
                default=[],
                metavar="WRONG=RIGHT",
                help=f.metadata["help"] + " (repeatable; merged over the config file)",
            )
            continue
        flag = "--" + f.name.replace("_", "-")
        if f.type == "bool":
            parser.add_argument(
                flag,
                action=argparse.BooleanOptionalAction,
                default=None,
                help=f.metadata["help"],
            )
        else:
            parser.add_argument(
                flag,
                type=_CLI_TYPES[f.type.replace(" | None", "")],
                default=None,
                help=f.metadata["help"],
            )


def apply_cli_overrides(config: Config, args: argparse.Namespace) -> Config:
    updates = {
        f.name: getattr(args, f.name)
        for f in fields(Config)
        if f.name != "replacements" and getattr(args, f.name, None) is not None
    }
    if args.replace:
        updates["replacements"] = {**config.replacements, **dict(args.replace)}
    return replace(config, **updates)


def describe_keys() -> str:
    """Render the config key reference for --help, generated from the
    dataclass so the listing can never go stale."""
    out: list[str] = []
    for f in fields(Config):
        if f.name == "replacements":
            out.append("")
            decl = f"  [{f.name}]"
        elif not f.metadata["show_default"]:
            decl = f"  {f.name} = (see README)"
        else:
            decl = f"  {f.name} = {_toml_literal(f.default)}"
        first, *rest = textwrap.wrap(f.metadata["help"], width=_HELP_WRAP) or [""]
        out.append(f"{decl.ljust(_COMMENT_COL - 1)} # {first}")
        out.extend(f"{' ' * (_COMMENT_COL - 1)} # {line}" for line in rest)
    return "\n".join(out)
