# Bundled build toolchains

## Decision

`retrolarium-kit` owns the compiler identities required by its generated and
authoring projects. A normal wheel installation carries the payload bytes; first
use materializes them from package data into a writable cache and checks every
size and SHA-256 before returning a command path. There is no implicit network
access and no fallback to an uncontrolled `PATH` executable.

The initial release is deliberately smaller than the full set of toolchains the
game disassemblies have used over time:

| Suite       | Component    | Identity                 | Windows x86-64 | Linux x86-64 |
| ----------- | ------------ | ------------------------ | -------------: | -----------: |
| `nes`       | ca65 + ld65  | cc65 2.19, Git `0fca835` |       complete |  unavailable |
| `megadrive` | Flamewing AS | 1.42 Beta, build 212     |       complete |     complete |
| `megadrive` | p2bin        | Clownacy `e26d8aa8...`   |       complete |     complete |

This covers Castle Excellent, Castlequest, Doraemon, Pac-Man, SMB1 and Solomon's
Key on NES, plus the audited Flicky and Alien Soldier AS/p2bin build contract.
It also covers the common build-212 toolchain used by S1/SK-derived projects.
The two console suites are complete on the primary Windows host.

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

## Rights and provenance

- cc65 is redistributed under its zlib license. The two Windows binaries are the
  byte-identical 2.19/`0fca835` pair present in five audited NES projects.
- AS is GPL-2.0-or-later. The audited binaries are Flamewing's modified AS, not
  the byte-different official Alfred Arnold build. The package therefore carries
  the complete Flamewing source snapshot corresponding to each host:
  `721a327a...` for the newer Windows binary and `3a5d200f...` for the Linux
  binary imported earlier from PR #25. It also carries the message catalogs and
  GPL text.
- p2bin is the ISC-licensed Clownacy fork pinned to commit
  `e26d8aa8c43e285bac5e3b7df3be1adae515994f`.

The authoritative machine-readable record is
`src/retrolarium_kit/core/toolchains/manifest.json`. It records package paths,
installed paths, sizes, hashes, licenses, corresponding source revisions, donor
repository commits and supported hosts. In particular, AS comes from the
`s1_improvements` import commits `10e77929...` (Windows) and `1ce386fc...`
(Linux); p2bin records its separate per-host import commits, while the cc65 pair
is traced to the initial Castle Excellent reconstruction commit.

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
compatibility override using the manifest's literal argv array.

The native gate currently rebuilds real modular emitter output byte-identically
for NES, SNES and Mega Drive. This validates AS-specific `binclude` output as
well as the compiler probes.

The complete Castle Excellent source at commit `72e08e03...` was also copied to
an ignored clean workspace without its repository-local `bin/`, previous build
directory or ROM inputs. The materialized NES suite passed the source project's
own pinned-binary audit and produced the canonical 65,552-byte image with
SHA-256 `fd6214cdc441070b9edab74b9c31ad16007bd0873e32c0c883de4544ca69f261`.
Neither the source checkout nor the resulting image is tracked here.

## Next compatibility gates

1. Build Flicky using materialized AS/p2bin with its repo-local compiler
   directory hidden.
2. Produce and smoke-test Linux cc65 payloads before marking the Linux NES suite
   complete.
3. Add modified AS and historical Shining Force profiles only alongside exact
   build fixtures and their complete GPL corresponding sources.
