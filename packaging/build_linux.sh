#!/usr/bin/env bash
# Build Linux onedir + .tar.gz (+ optional .deb) for Ubuntu/Debian.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="${VERSION:-}"
if [[ -z "$VERSION" ]]; then
  VERSION="$(python - <<'PY'
from pathlib import Path
import re
text = Path("pyproject.toml").read_text(encoding="utf-8")
m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
print(m.group(1) if m else "0.0.0")
PY
)"
fi

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH_TAG="amd64" ;;
  aarch64|arm64) ARCH_TAG="arm64" ;;
  *) ARCH_TAG="$ARCH" ;;
esac

OUT_DIR="${OUT_DIR:-$ROOT/dist}"
STAGE="$OUT_DIR/ydna"
TARBALL="$OUT_DIR/make-dna-great-again-${VERSION}-linux-${ARCH_TAG}.tar.gz"

echo "==> version=$VERSION arch=$ARCH_TAG"
echo "==> installing build deps"
python -m pip install -U pip wheel
python -m pip install -e ".[dev]" 2>/dev/null || python -m pip install -e .
python -m pip install "pyinstaller>=6.3"

echo "==> pyinstaller"
rm -rf "$ROOT/build/ydna" "$STAGE"
python -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$OUT_DIR" \
  --workpath "$ROOT/build" \
  "$ROOT/packaging/ydna.spec"

test -x "$STAGE/ydna"

echo "==> smoke: --help"
"$STAGE/ydna" --help >/dev/null

echo "==> tarball"
rm -f "$TARBALL"
tar -C "$OUT_DIR" -czf "$TARBALL" ydna
echo "wrote $TARBALL"

if [[ "${MAKE_DEB:-1}" == "1" ]]; then
  bash "$ROOT/packaging/make_deb.sh" "$VERSION" "$ARCH_TAG" "$STAGE" "$OUT_DIR"
fi

ls -lh "$OUT_DIR"/make-dna-great-again-"${VERSION}"-linux-"${ARCH_TAG}".*
