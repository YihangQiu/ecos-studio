#!/usr/bin/env bash
# Create dev symlinks for Tauri externalBin and resources.
# Usage: bazel run //ecos:dev_symlinks
set -euo pipefail

WS="${BUILD_WORKSPACE_DIRECTORY:?Must run via: bazel run //ecos:dev_symlinks}"
cd "$WS"

require_cmd() {
    if ! command -v "$1" > /dev/null 2>&1; then
        echo "ERROR: $1 not found in PATH." >&2
        exit 1
    fi
}

make_link() {
    local dir="$1" name="$2" target="$3"
    mkdir -p "$dir"
    local link="$dir/$name"
    ln -sfn "$target" "$link"
    echo "==> Linked: $link -> $target"
}

require_cmd bazel
require_cmd rustc

TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"
echo "==> Target triple: $TRIPLE"

# Resolve api-server binary path via cquery
echo "==> Resolving api-server binary path..."
API_SERVER_PATH="$(bazel cquery --output=files //ecos:build_ecos_server 2>/dev/null | head -n1)"
if [[ -z "$API_SERVER_PATH" ]]; then
    echo "ERROR: Could not resolve //ecos:build_ecos_server output. Run 'bazel build //ecos:build_ecos_server' first." >&2
    exit 1
fi
API_SERVER_ABS="$(realpath "$API_SERVER_PATH")"

# Resolve oss_cad_suite_pruned directory via output_base (name may vary with Bzlmod suffix)
echo "==> Resolving oss_cad_suite_pruned path..."
OUTPUT_BASE="$(bazel info output_base 2>/dev/null)"
OSS_CAD_DIR="$(find "$OUTPUT_BASE/external" -maxdepth 1 -type d -name '*oss_cad_suite_pruned*' 2>/dev/null | head -n1)"
if [[ -z "$OSS_CAD_DIR" || ! -d "$OSS_CAD_DIR" ]]; then
    echo "ERROR: oss_cad_suite_pruned external directory not found under: $OUTPUT_BASE/external/" >&2
    echo "       Try: bazel fetch @oss_cad_suite_pruned//:all_files" >&2
    exit 1
fi

TAURI_DIR="$WS/ecos/gui/src-tauri"
make_link "$TAURI_DIR/binaries"  "api-server-$TRIPLE" "$API_SERVER_ABS"
make_link "$TAURI_DIR/resources"  "oss-cad-suite"      "$OSS_CAD_DIR/oss-cad-suite"

echo
echo "Done. Dev symlinks created."
