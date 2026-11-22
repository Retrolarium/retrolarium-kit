#!/bin/bash
# The POSIX form of tools/build_toolchains.ps1: builds the assemblers the kit
# ships (cc65's ca65 and ld65, the AS macro assembler, p2bin) for Windows
# x86-64 and Linux x86-64 in Docker, from the AS source archive in
# core/toolchains/sources and cc65 and p2bin at the commits
# tools/toolchains/build.sh pins, into core/toolchains/payloads. The image
# installs its compilers from the Debian snapshot the Dockerfile pins, so the
# result must match the digests in core/toolchains/manifest.json; check with
# `python tools/check_toolchain_payloads.py` or `rltool build-tools status`.
#
#     tools/build_toolchains.sh [JOBS]
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
toolchains="$here/../src/retrolarium_kit/core/toolchains"
sources="$(cd "$toolchains/sources" && pwd)"
mkdir -p "$toolchains/payloads"
payloads="$(cd "$toolchains/payloads" && pwd)"
image=retrolarium-toolchains

docker build -t "$image" "$here/toolchains"
# As the calling user, so the payloads are not root's.
arguments=(run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp)
arguments+=(--mount "type=bind,\"source=$sources\",target=/sources,readonly")
arguments+=(--mount "type=bind,\"source=$payloads\",target=/out")
if [ "${1:-0}" -gt 0 ]; then
    arguments+=(-e "JOBS=$1")
fi
docker "${arguments[@]}" "$image"
echo "Wrote the assemblers to $payloads"
