{
  description = "LearnHouse - môi trường phát triển";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # Thư viện runtime cho các wheel Python dựng sẵn (psycopg2-binary, pillow, cryptography...)
        # và cho Python do uv tải về (api yêu cầu chính xác 3.14.7).
        runtimeLibs = with pkgs; [
          stdenv.cc.cc.lib
          zlib
          openssl
          libffi
          libpq
        ];

        # DB/Redis, env file, cài dependency và chạy api/web/collab đều do
        # `learnhouse dev` (apps/cli/src/commands/dev.ts) đảm nhiệm — flake chỉ lo toolchain.
        # `lh <command> [options]` = CLI build từ source (vd: lh dev, lh doctor, lh --help).
        lh = pkgs.writeShellScriptBin "lh" ''
          set -euo pipefail
          root="$(git rev-parse --show-toplevel)"
          cli="$root/apps/cli"
          dist="$cli/dist/bin/learnhouse.js"
          if [ ! -d "$cli/node_modules" ]; then (cd "$cli" && bun install) >&2; fi
          if [ ! -f "$dist" ] || [ -n "$(find "$cli/bin" "$cli/src" -newer "$dist" -print -quit)" ]; then
            (cd "$cli" && bun run build) >&2
          fi
          exec node "$dist" "$@"
        '';

        # `learnhouse dev` không có chức năng reset. Không tự bật lại container:
        # CLI chỉ hỏi và tạo admin khi DB/Redis chưa chạy.
        dbReset = pkgs.writeShellScriptBin "lh-db-reset" ''
          set -euo pipefail
          if ! docker info >/dev/null 2>&1; then
            echo "Docker chưa chạy hoặc không truy cập được." >&2
            exit 1
          fi
          if [ "''${1:-}" != "-y" ]; then
            echo "Sẽ xoá container và volume Docker của PostgreSQL, Redis (project learnhouse-dev)."
            read -rp "Tiếp tục? [y/N] " ans
            [ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "Đã huỷ."; exit 1; }
          fi
          docker compose -p learnhouse-dev down -v
          echo "Đã reset sạch dữ liệu dev. Chạy lh dev để khởi tạo lại (sẽ hỏi admin email/password)."
        '';
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            # Frontend / collab / cli / docs (bun 1.4.0 theo .bun-version)
            bun
            nodejs_22

            # Backend
            uv

            # Tiện ích
            git
            curl
            pkg-config
            lh
            dbReset
          ] ++ runtimeLibs;

          env = {
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;
            # Để uv tự tải Python đúng phiên bản pyproject yêu cầu
            UV_PYTHON_DOWNLOADS = "automatic";
            UV_PYTHON_PREFERENCE = "managed";
          };

          shellHook = ''
            echo "🏠 LearnHouse dev shell"
            echo "  bun $(bun --version) | node $(node --version) | uv $(uv --version | cut -d' ' -f2)"
            if [ "$(bun --version)" != "$(cat .bun-version 2>/dev/null)" ]; then
              echo "  ⚠ bun khác .bun-version ($(cat .bun-version))"
            fi
            echo "  lh <command> [options] : LearnHouse CLI từ source (lh dev [--ee], lh --help)"
            echo "  lh-db-reset [-y] : xoá sạch DB và Redis"
            echo "  psql     : docker exec -it learnhouse-db-dev psql -U learnhouse"
            echo "  Tắt DB   : docker compose -p learnhouse-dev down"
          '';
        };
      });
}
