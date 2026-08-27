{
  description = "Japanese push-to-talk voice input with local ASR. Copies the transcription to the clipboard (optional auto-paste)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { self, nixpkgs }:
    let
      # MLXを使うためApple Siliconのみ
      system = "aarch64-darwin";
      pkgs = nixpkgs.legacyPackages.${system};

      # Fixes an ABBA deadlock between Pa_StopStream and the CoreAudio IO
      # thread on macOS. Drop this and the DYLD_LIBRARY_PATH export once
      # https://github.com/PortAudio/portaudio/pull/1175 is released and
      # picked up by the dylib bundled with the sounddevice wheel.
      portaudioPatched = pkgs.portaudio.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ [
          ./patches/portaudio-pr1175-coreaudio-stop-deadlock.diff
        ];
      });

      # MLX系のwheelはnixpkgsに無いので、純Nixでは閉じずにuvへ委譲する。
      # 依存はuv.lockで固定され、初回起動時に ~/.cache/koeuchi/venv へ展開される
      koeuchi = pkgs.writeShellApplication {
        name = "koeuchi";
        runtimeInputs = [ pkgs.uv ];
        text = ''
          venv="''${XDG_CACHE_HOME:-$HOME/.cache}/koeuchi/venv"
          export UV_PROJECT_ENVIRONMENT="$venv"
          export UV_PYTHON="${pkgs.python312}/bin/python3.12"
          export UV_PYTHON_DOWNLOADS=never
          # sounddevice prefers a dylib found via find_library over the one
          # bundled in the wheel, so inject the patched portaudio here
          export DYLD_LIBRARY_PATH="${portaudioPatched}/lib''${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
          uv sync --locked --no-editable --quiet --project ${self}
          exec "$venv/bin/koeuchi" "$@"
        '';
      };
    in
    {
      packages.${system}.default = koeuchi;

      apps.${system}.default = {
        type = "app";
        program = "${koeuchi}/bin/koeuchi";
      };

      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pkgs.uv
          pkgs.python312
        ];
      };
    };
}
