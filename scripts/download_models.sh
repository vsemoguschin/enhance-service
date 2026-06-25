#!/usr/bin/env bash
# Download realesrgan-ncnn-vulkan binary + models into ./bin and ./models (not in git).
# Default target: Linux (codex). For local macOS dev, pass: PLATFORM=macos ./download_models.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM="${PLATFORM:-ubuntu}"   # ubuntu | macos | windows
VER="20220424"
BASE="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0"
ZIP="realesrgan-ncnn-vulkan-${VER}-${PLATFORM}.zip"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading ${ZIP} ..."
curl -fL -o "$TMP/r.zip" "${BASE}/${ZIP}"
unzip -oq "$TMP/r.zip" -d "$TMP/r"

# zip layout: realesrgan-ncnn-vulkan + models/ at the archive root (in a subdir on some builds)
SRC="$TMP/r"
[ -f "$SRC/realesrgan-ncnn-vulkan" ] || SRC="$(dirname "$(find "$TMP/r" -name realesrgan-ncnn-vulkan | head -1)")"

mkdir -p "$DIR/bin" "$DIR/models"
cp "$SRC/realesrgan-ncnn-vulkan" "$DIR/bin/"
chmod +x "$DIR/bin/realesrgan-ncnn-vulkan"
cp "$SRC/models/"*.param "$SRC/models/"*.bin "$DIR/models/"

if [ "$PLATFORM" = "macos" ]; then
  xattr -cr "$DIR/bin/realesrgan-ncnn-vulkan" 2>/dev/null || true  # clear macOS quarantine
fi

echo "Done. bin/ and models/ ready:"
ls -1 "$DIR/models" | sed 's/^/  /'
echo
echo "NOTE (Linux/codex, no GPU): realesrgan-ncnn-vulkan needs a Vulkan ICD."
echo "  On a CPU-only VM install software Vulkan (lavapipe):"
echo "    sudo apt-get install -y libvulkan1 mesa-vulkan-drivers"
echo "  Verify a device is visible:  vulkaninfo --summary  (or the binary will error 'no vulkan device')."
