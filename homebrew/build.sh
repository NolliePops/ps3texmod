#!/usr/bin/env bash
# Build ps3texmod for the PS3 with PSL1GHT.
#
# Requirements:
#   * ps3dev toolchain + PSL1GHT (https://github.com/ps3dev/ps3dev)
#   * make, a C compiler (ppu-gcc), pkg/sfo/make_self/fself/package_finalize
#
# Usage:
#   export PS3DEV=/usr/local/ps3dev
#   export PSL1GHT=$PS3DEV/psl1ght
#   ./build.sh              # build SELF + fake SELF
#   ./build.sh --pkg        # also build the installable .pkg
set -euo pipefail
cd "$(dirname "$0")"

if [[ -z "${PSL1GHT:-}" ]]; then
    echo "error: PSL1GHT is not set (export PSL1GHT=\$PS3DEV/psl1ght)" >&2
    exit 2
fi
if [[ -z "${PS3DEV:-}" ]]; then
    echo "error: PS3DEV is not set (export PS3DEV=/usr/local/ps3dev)" >&2
    exit 2
fi
for tool in make ppu-gcc; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: $tool not found; add \$PS3DEV/bin and \$PS3DEV/ppu/bin to PATH" >&2
        exit 2
    fi
done

export PATH="$PS3DEV/bin:$PS3DEV/ppu/bin:$PATH"

make clean
make -j"${JOBS:-4}"

if [[ "${1:-}" == "--pkg" ]]; then
    make pkg
fi

echo
echo "artifacts:"
for f in ps3texmod.elf ps3texmod.self ps3texmod.fake.self ps3texmod.pkg ps3texmod.gnpdrm.pkg; do
    [[ -f "$f" ]] && ls -l "$f"
done
