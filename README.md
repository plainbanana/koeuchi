# koeuchi

Japanese push-to-talk voice input, fully local.

**Hold the right Option key to record** → local ASR transcribes → the result goes to the clipboard.

## Requirements

- Apple Silicon Mac (MLX does not run on Intel Macs)
- [Nix](https://nixos.org/) or [uv](https://docs.astral.sh/uv/)

## Setup

Nix:

```bash
nix run github:plainbanana/koeuchi
```

Or uv (Python 3.12 is provisioned automatically):

```bash
git clone <this repository> && cd koeuchi
uv sync --locked
uv run koeuchi
```

## Model

[ph0ryn/Qwen3-ASR-1.7B-JA-MLX-bf16](https://huggingface.co/ph0ryn/Qwen3-ASR-1.7B-JA-MLX-bf16) —
an MLX conversion of
[neosophie/Qwen3-ASR-1.7B-JA](https://huggingface.co/neosophie/Qwen3-ASR-1.7B-JA)
(Qwen3-ASR-1.7B fine-tuned on Japanese IT terminology).

## Usage

1. Keep `uv run koeuchi` running
2. Bring the paste target to the front
3. Hold the right Option key, speak, release
4. The transcription lands in the clipboard once the Glass completion sound plays; with `auto_paste = true` it is also pasted (Cmd+V) into the frontmost app

## Recording overlay

While recording, a floating overlay shows the stop button, mic level, and
elapsed time. After recording, it shows transcription progress and the result,
then disappears. It never steals focus and can be disabled with `overlay = false`.

## Configuration

`~/.config/koeuchi/config.toml` (defaults are used if absent):

```toml
hotkey = "alt_r"          # push-to-talk key; attribute name of pynput.keyboard.Key
model = "ph0ryn/Qwen3-ASR-1.7B-JA-MLX-bf16" # HuggingFace repository name or path to a local MLX model
model_revision = ""       # pin a commit hash (the default model is already pinned; see asr/qwen3_asr.py)
language = "Japanese"     # recognition language; "" lets the model auto-detect
auto_paste = false        # true = also auto-paste (Cmd+V) into the frontmost app.
                          # Requires Accessibility permission for the app that launches koeuchi
min_duration = 0.3        # recordings shorter than this are discarded (seconds)
sounds = true             # audio cues: start (Tink) / stop (Pop) / done (Glass) / error (Basso)
overlay = true            # show the level meter + stop button pill while recording
menu_bar = true           # menu bar icon: mic (idle) / red mic (recording) / waveform (transcribing)

# Deterministic replacements applied to the transcription. Use this to fix terminology
[replacements]
"フォースプッシュ" = "force push"
```

### language

`"Japanese"` by default; other languages can be set in the config
(`English`, `Chinese`, etc. — see `support_languages` in the model's config.json).

