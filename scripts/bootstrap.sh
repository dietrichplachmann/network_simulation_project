#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
chmod +x "$root/scripts/cmake" "$root/scripts/ctest"
revision=4059af204636298c061df368e241941fa3ff97bf
for tool in git cmake c++; do
    command -v "$tool" >/dev/null || { echo "Missing $tool. See README.md for prerequisites." >&2; exit 1; }
done
mkdir -p "$root/.deps"
if [[ ! -d "$root/.deps/ns-3/.git" ]]; then
    git clone --depth 1 --branch ns-3.45 https://github.com/nsnam/ns-3-dev-git.git "$root/.deps/ns-3"
fi
if [[ "$(git -C "$root/.deps/ns-3" rev-parse HEAD)" != "$revision" ]]; then
    echo "Expected ns-3.45 ($revision) in .deps/ns-3. Refusing to change an existing checkout." >&2
    exit 1
fi
cmake -S "$root/.deps/ns-3" -B "$root/.deps/ns-3-build" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$root/.deps/install" \
    -DNS3_ENABLED_MODULES='core;network;internet;mobility;wifi;applications;olsr;flow-monitor' \
    -DNS3_EXAMPLES=OFF -DNS3_TESTS=OFF -DNS3_PYTHON_BINDINGS=OFF \
    -DNS3_WARNINGS_AS_ERRORS=OFF -DNS3_PRECOMPILE_HEADERS=OFF
cmake --build "$root/.deps/ns-3-build" --parallel "${JOBS:-2}"
cmake --install "$root/.deps/ns-3-build"
cmake -S "$root" -B "$root/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$root/build" --parallel "${JOBS:-2}"
