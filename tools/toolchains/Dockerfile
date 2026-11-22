# The compilers that build the assemblers the kit ships: Debian's GCC for
# Linux x86-64 and its MinGW-w64 GCC for Windows x86-64. See
# tools/build_toolchains.ps1 and tools/build_toolchains.sh.
FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

# The packages come from the Debian archive as it was at SNAPSHOT
# (snapshot.debian.org), so a rebuild installs the same compiler versions.
# write_manifest.py records the date in core/toolchains/manifest.json.
ARG SNAPSHOT=20261011T000000Z

RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
        "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${SNAPSHOT} bookworm main" \
        "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${SNAPSHOT} bookworm-updates main" \
        "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/${SNAPSHOT} bookworm-security main" \
        > /etc/apt/sources.list \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get install -y --no-install-recommends \
        ca-certificates git make cmake gcc g++ libc6-dev \
        gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 binutils-mingw-w64-x86-64 mingw-w64-x86-64-dev \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

COPY build.sh /usr/local/bin/build-toolchains
ENTRYPOINT ["bash", "/usr/local/bin/build-toolchains"]
