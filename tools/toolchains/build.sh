#!/bin/bash
# Runs inside the image from tools/toolchains/Dockerfile and builds every
# assembler the kit ships, for Windows x86-64 and Linux x86-64, into /out
# (the kit's core/toolchains/payloads). /sources holds the kit's AS source
# archive (read only); cc65 and p2bin are cloned at pinned commits. Windows
# programs link statically and stripped without a build timestamp, and Linux
# programs link statically and stripped, so a rebuild gives the same files.
set -euo pipefail

CC65_COMMIT=0fca83500b1a32fa1758bf2484562a00381f5642
P2BIN_COMMIT=e26d8aa8c43e285bac5e3b7df3be1adae515994f
ASL_ARCHIVE=/sources/asl-flamewing-721a327a803585e2d1f59acac93cbd0768213d06.tar.gz
WINDOWS=x86_64-w64-mingw32
WINDOWS_LINK="-static -s -Wl,--no-insert-timestamp"
LINUX_LINK="-static -s"
JOBS="${JOBS:-$(nproc)}"

work=/tmp/toolchains
rm -rf "$work"
mkdir -p "$work"

# cc65: ca65 and ld65, named as before ("V2.19 - Git 0fca835"; left to
# itself the makefile asks git, whose short form can grow).
CC65_BUILD_ID="Git ${CC65_COMMIT:0:7}"
git clone --quiet https://github.com/cc65/cc65 "$work/cc65-linux"
git -C "$work/cc65-linux" checkout --quiet "$CC65_COMMIT"
git clone --quiet "$work/cc65-linux" "$work/cc65-windows"
git -C "$work/cc65-windows" checkout --quiet "$CC65_COMMIT"
make -C "$work/cc65-linux/src" -j"$JOBS" ca65 ld65 LDFLAGS="$LINUX_LINK" BUILD_ID="$CC65_BUILD_ID"
make -C "$work/cc65-windows/src" -j"$JOBS" ca65 ld65 CROSS_COMPILE="$WINDOWS-" LDFLAGS="$WINDOWS_LINK" BUILD_ID="$CC65_BUILD_ID"

# p2bin, with its compressor submodules.
git clone --quiet --recursive https://github.com/Clownacy/p2bin "$work/p2bin"
git -C "$work/p2bin" checkout --quiet "$P2BIN_COMMIT"
git -C "$work/p2bin" submodule --quiet update --init --recursive
cmake -S "$work/p2bin" -B "$work/p2bin-linux" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_EXE_LINKER_FLAGS="$LINUX_LINK"
cmake --build "$work/p2bin-linux" -j"$JOBS"
cmake -S "$work/p2bin" -B "$work/p2bin-windows" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_SYSTEM_PROCESSOR=x86_64 -DCMAKE_C_COMPILER="$WINDOWS-gcc" \
    -DCMAKE_EXE_LINKER_FLAGS="$WINDOWS_LINK"
cmake --build "$work/p2bin-windows" -j"$JOBS"

# The AS macro assembler from the kit's source archive.
mkdir -p "$work/asl"
tar -xzf "$ASL_ARCHIVE" -C "$work/asl" --strip-components=1
cmake -S "$work/asl" -B "$work/asl-linux" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_EXE_LINKER_FLAGS="$LINUX_LINK"
cmake --build "$work/asl-linux" -j"$JOBS"
# The Windows build runs the Linux build's message compiler.
cmake -S "$work/asl" -B "$work/asl-windows" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_SYSTEM_PROCESSOR=x86_64 -DCMAKE_C_COMPILER="$WINDOWS-gcc" \
    -DCMAKE_EXE_LINKER_FLAGS="$WINDOWS_LINK" \
    -DIMPORT_EXECUTABLES="$work/asl-linux/ImportExecutables.cmake"
cmake --build "$work/asl-windows" -j"$JOBS" --target asl

# The payloads, laid out as the kit's manifest names them.
for host in linux-x86_64 windows-x86_64; do
    rm -rf "/out/$host"
    mkdir -p "/out/$host/cc65" "/out/$host/asl" "/out/$host/p2bin"
done
cp "$work/cc65-linux/bin/ca65" "$work/cc65-linux/bin/ld65" /out/linux-x86_64/cc65/
cp "$work/cc65-windows/bin/ca65.exe" "$work/cc65-windows/bin/ld65.exe" /out/windows-x86_64/cc65/
cp "$work/p2bin-linux/p2bin" /out/linux-x86_64/p2bin/
cp "$work/p2bin-windows/p2bin.exe" /out/windows-x86_64/p2bin/
for file in as.msg cmdarg.msg ioerrs.msg; do
    cp "$work/asl-linux/$file" /out/linux-x86_64/asl/
    cp "$work/asl-windows/$file" /out/windows-x86_64/asl/
done
cp "$work/asl-linux/asl" /out/linux-x86_64/asl/
cp "$work/asl-windows/asl.exe" /out/windows-x86_64/asl/
cd /out && find linux-x86_64 windows-x86_64 -type f | sort | xargs sha256sum
