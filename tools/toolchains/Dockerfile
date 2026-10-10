# The compilers that build the assemblers the kit ships: Debian's GCC for
# Linux x86-64 and its MinGW-w64 GCC for Windows x86-64. See
# tools/build_toolchains.ps1.
FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates git make cmake gcc g++ libc6-dev \
        gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 binutils-mingw-w64-x86-64 mingw-w64-x86-64-dev \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

COPY build.sh /usr/local/bin/build-toolchains
ENTRYPOINT ["bash", "/usr/local/bin/build-toolchains"]
