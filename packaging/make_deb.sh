#!/usr/bin/env bash
# Wrap a PyInstaller onedir tree into a simple .deb for Ubuntu/Debian.
set -euo pipefail

VERSION="${1:?version}"
ARCH_TAG="${2:?arch}"
STAGE="${3:?onedir path}"
OUT_DIR="${4:?output dir}"

PKG_NAME="make-dna-great-again"
DEB_ROOT="$(mktemp -d)"
trap 'rm -rf "$DEB_ROOT"' EXIT

INSTALL_ROOT="/opt/${PKG_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p \
  "$DEB_ROOT/DEBIAN" \
  "$DEB_ROOT${INSTALL_ROOT}" \
  "$DEB_ROOT/usr/bin" \
  "$DEB_ROOT/usr/share/applications" \
  "$DEB_ROOT/usr/share/doc/${PKG_NAME}"

cp -a "$STAGE"/. "$DEB_ROOT${INSTALL_ROOT}/"
chmod 755 "$DEB_ROOT${INSTALL_ROOT}/ydna"

cat >"$DEB_ROOT/usr/bin/ydna" <<EOF
#!/bin/sh
exec ${INSTALL_ROOT}/ydna "\$@"
EOF
chmod 755 "$DEB_ROOT/usr/bin/ydna"
ln -sf ydna "$DEB_ROOT/usr/bin/mdga"

if [[ -f "$SCRIPT_DIR/make-dna-great-again.desktop" ]]; then
  install -m 644 "$SCRIPT_DIR/make-dna-great-again.desktop" \
    "$DEB_ROOT/usr/share/applications/make-dna-great-again.desktop"
fi

cat >"$DEB_ROOT/usr/share/doc/${PKG_NAME}/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: make-dna-great-again
Comment: Local TUI for personal genotype analysis.
EOF

SIZE_KB="$(du -sk "$DEB_ROOT" | awk '{print $1}')"
cat >"$DEB_ROOT/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: science
Priority: optional
Architecture: ${ARCH_TAG}
Installed-Size: ${SIZE_KB}
Maintainer: Make DNA Great Again <noreply@localhost>
Depends: libc6
Description: Make DNA Great Again — local genotype TUI
 Terminal UI for Y-DNA tree, GWAS/PGS, and local Gemini chat.
 Data stays on your machine under ~/.config/make-dna-great-again/.
EOF

DEB="$OUT_DIR/${PKG_NAME}_${VERSION}_${ARCH_TAG}.deb"
rm -f "$DEB"
dpkg-deb --root-owner-group --build "$DEB_ROOT" "$DEB"
echo "wrote $DEB"
