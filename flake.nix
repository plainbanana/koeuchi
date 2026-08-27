{
  description = "Japanese push-to-talk voice input with local ASR. Copies the transcription to the clipboard (optional auto-paste)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { self, nixpkgs }:
    let
      # MLXを使うためApple Siliconのみ
      system = "aarch64-darwin";
      pkgs = nixpkgs.legacyPackages.${system};

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
