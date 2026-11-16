#!/usr/bin/env bash
# Install the routers the benchmark runs, at the versions the published results used.
# Linux x86-64. KiCad 10 (kicad-cli and a python that imports pcbnew) and Node 22+
# are expected on the machine already.
#
#   tools/install.sh freerouting   # Freerouting 2.5.0 + a Java 25 runtime
#   tools/install.sh node          # tscircuit capacity autorouter + KiCad Routing Tools (npm)
#   tools/install.sh tracemaker    # TraceMaker 0.9.0, built from source (CUDA 12+, CMake 3.28+, Ninja, g++ 13)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"

FREEROUTING_URL="https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0.jar"
FREEROUTING_SHA256="f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7"
JDK_URL="https://api.adoptium.net/v3/binary/latest/25/ga/linux/x64/jdk/hotspot/normal/eclipse"
TRACEMAKER_REPO="https://github.com/DingoOz/TraceMaker.git"
TRACEMAKER_COMMIT="c6fcb7b4912bdabefeb7f4191c011b1a086335e0"

freerouting() {
  curl -fL -o "$HERE/freerouting-2.5.0.jar" "$FREEROUTING_URL"
  echo "$FREEROUTING_SHA256  $HERE/freerouting-2.5.0.jar" | sha256sum -c -
  mkdir -p "$HERE/jdk25"
  curl -fL "$JDK_URL" | tar -xz -C "$HERE/jdk25" --strip-components=1
  "$HERE/jdk25/bin/java" -version
  echo "export JAVA=$HERE/jdk25/bin/java FREEROUTING_JAR=$HERE/freerouting-2.5.0.jar"
}

node_tools() {
  (cd "$ROOT" && npm install --no-audit --no-fund)
}

tracemaker() {
  # Build dependencies (Boost, TBB, Eigen, fmt, spdlog, nlohmann_json, CLI11, flatbuffers,
  # pybind11) come from the system or a conda environment on CMAKE_PREFIX_PATH.
  if [ ! -d "$HERE/TraceMaker" ]; then git clone "$TRACEMAKER_REPO" "$HERE/TraceMaker"; fi
  cd "$HERE/TraceMaker"
  git fetch -q origin && git checkout -q "$TRACEMAKER_COMMIT"
  # Warnings are not errors: the upstream build treats them as errors, which fails on g++ 13.
  sed -i 's/list(APPEND cxx_warnings -Werror)/list(APPEND cxx_warnings)/; s/\$<\$<COMPILE_LANGUAGE:CUDA>:-Werror=all-warnings>/$<$<COMPILE_LANGUAGE:CUDA>:>/' cmake/Warnings.cmake
  cmake --preset release ${CMAKE_EXTRA_ARGS:-}
  cmake --build --preset release -j "$(nproc)" --target tracemaker
  echo "export TRACEMAKER=$HERE/TraceMaker/build/release/src/app/tracemaker"
}

case "${1:-}" in
  freerouting) freerouting ;;
  node) node_tools ;;
  tracemaker) tracemaker ;;
  *) echo "usage: $0 freerouting|node|tracemaker" >&2; exit 2 ;;
esac
