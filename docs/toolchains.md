# Bundled build toolchains

## Decision

The current public binary distribution is the Windows Studio package only. Linux
payloads and wheels are development builds and are not part of that release. The
current Linux recipe links glibc statically; publishing a Linux wheel requires a
separate distribution review, complete LGPL notices and corresponding
source/relinking material, or a reviewed alternative linkage. Do not infer Linux
release readiness from passing development tests.

`retrolarium-kit` owns the compiler identities required by its generated and
authoring projects. A normal wheel installation carries the payload bytes; first
use materializes them from package data into a writable cache and checks every
size and SHA-256 before returning a command path. There is no implicit network
access and no fallback to an uncontrolled `PATH` executable.

The release is deliberately smaller than the full set of toolchains the game
disassemblies have used over time:

| Suite       | Component    | Identity                 | Windows x86-64 | Linux x86-64 |
| ----------- | ------------ | ------------------------ | -------------: | -----------: |
| `nes`       | ca65 + ld65  | cc65 2.19, Git `0fca835` |       complete |     complete |
| `megadrive` | Flamewing AS | 1.42 Beta, build 212     |       complete |     complete |
| `megadrive` | p2bin        | Clownacy `e26d8aa8...`   |       complete |     complete |

This covers Castle Excellent, Castlequest, Doraemon, Pac-Man, SMB1 and Solomon's
Key on NES, plus the Flicky and Alien Soldier AS/p2bin build contract. It also
covers the common build-212 toolchain used by S1/SK-derived projects.

## Why not copy every executable named `asw.exe`

The local corpus contains four distinct Windows AS executables:

- the current Flamewing build 212 used by Flicky, Alien Soldier and the
  S1/SK-derived projects;
- a different modified build-212 family used by the S2 project;
- historical build 58 in the Shining Force I project;
- build 228 plus companion utilities in the Shining Force II project.

Their byte identities are not interchangeable merely because they accept a
similar dialect. Legacy profiles will be added only with a project fixture that
proves which commands are required and whether the output remains exact. This
prevents a larger but ambiguous wheel from silently selecting the wrong
assembler.

## How the payloads are built

Every payload is built from source by `tools/toolchains/build.sh`, in the image
of `tools/toolchains/Dockerfile`, for both hosts in one run:

- cc65's `ca65` and `ld65` from <https://github.com/cc65/cc65> at commit
  `0fca83500b1a32fa1758bf2484562a00381f5642`, with the build id `Git 0fca835`;
- AS (`asl` and its message catalogs) from the Flamewing source archive
  `asl-flamewing-721a327a803585e2d1f59acac93cbd0768213d06.tar.gz` in
  `core/toolchains/sources`, the same revision for Windows and Linux;
- p2bin from <https://github.com/Clownacy/p2bin> at commit
  `e26d8aa8c43e285bac5e3b7df3be1adae515994f`, with its submodules.

Linux programs link statically and stripped; Windows programs (MinGW-w64) link
statically and stripped without a build timestamp. The image is
`debian:bookworm-slim` pinned by digest, and its packages (GCC, MinGW-w64,
CMake) come from the Debian archive as it was at the `SNAPSHOT` date the
Dockerfile pins (`snapshot.debian.org`), so a rebuild installs the same compiler
versions and reproduces the same files. `manifest.json` records that image and
date under `build_environment`.

Build the payloads with `tools/build_toolchains.ps1` on Windows or
`tools/build_toolchains.sh` on Linux and macOS; both build the image and run
`build.sh` with `core/toolchains/sources` read-only and
`core/toolchains/payloads` as its output (the POSIX script runs the container as
the calling user). `tools/toolchains/write_manifest.py` writes the manifest
after a deliberate rebuild (a new image, snapshot or source commit); an
unchanged rebuild needs no new manifest. `rltool build-tools status` and
`python tools/check_toolchain_payloads.py` compare the payloads with it.

The payloads are not tracked. A wheel must carry them, so run
`python tools/check_toolchain_payloads.py` before `python -m build` or
`uv build`: it fails, naming every missing or changed file and the script that
builds them, when the payloads are incomplete.

## Rights and provenance

- cc65 is redistributed under its zlib license (`licenses/cc65-Zlib.txt`).
- AS is GPL-2.0-or-later. The package carries the complete Flamewing source
  archive the binaries are built from (`corresponding_sources` in the manifest),
  the message catalogs and the GPL text.
- p2bin is the ISC-licensed Clownacy fork.

The authoritative machine-readable record is
`src/retrolarium_kit/core/toolchains/manifest.json`: package paths, installed
paths, sizes, hashes, licenses, source revisions, supported hosts and the build
environment.

## API and cache boundary

`materialize_build_toolchain("nes")` returns `ca65` and `ld65` paths.
`materialize_build_toolchain("megadrive")` returns `asl`, `p2bin`, and the
required `AS_MSGPATH` environment. The default cache is under Local AppData on
Windows or the XDG cache root elsewhere; `RETROLARIUM_TOOLCHAIN_HOME` and the
explicit API argument can relocate it.

The package directory is treated as immutable. This matters for system Python
installs and ensures that restoring executable bits on Unix never mutates the
wheel installation. A cache entry is accepted only after all files match the
manifest.

## Native project build boundary

Generated NES and SNES manifests select the fixed `ca65-ld65-v1` recipe;
generated Mega Drive manifests select `asl-p2bin-v1`. `rltool project build`
materializes the declared suite and invokes its commands directly without a
shell, Make, Docker or a repository-local `bin/`. The build result records the
toolchain release, host and recipe. `--container IMAGE` remains an explicit
compatibility override using the manifest's literal argv array;
`--container default` selects the manifest's `default_container`. Docker runs
only with one of them: a build operation without a bundled recipe fails with
"this build needs a container: rerun with --container IMAGE" instead of starting
the declared image by itself. The container gets the project folder bind-mounted
at `/work` (the path quoted inside `--mount`; a path holding a double quote or a
line break is refused), runs as the calling user on POSIX (`--user uid:gid`) so
`build/` stays the user's, and is stopped when it exceeds the 15-minute build
bound (`core/containers.py`).

The native gate currently rebuilds real modular emitter output byte-identically
for NES, SNES and Mega Drive. This validates AS-specific `binclude` output as
well as the compiler probes.

The complete Castle Excellent source at commit `7a04b408...` was also copied to
an ignored clean workspace without its repository-local `bin/`, previous build
directory or ROM inputs. The materialized NES suite passed the source project's
own pinned-binary audit and produced the canonical 65,552-byte image with
SHA-256 `fd6214cdc441070b9edab74b9c31ad16007bd0873e32c0c883de4544ca69f261`.
Neither the source checkout nor the resulting image is tracked here.

## Next compatibility gates

1. Build Flicky using materialized AS/p2bin with its repo-local compiler
   directory hidden.
2. Add modified AS and historical Shining Force profiles only alongside exact
   build fixtures and their complete GPL corresponding sources.
