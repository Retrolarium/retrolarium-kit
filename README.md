# Retrolarium Kit

Retrolarium Kit is the independently installable, non-GUI command-line layer
behind Retrolarium Studio. It has no Qt dependency. It owns the hand-editable
`.rlj` Studio project, the declarative game profiles, the resource codecs of
every supported game (extract, validate, rebuild), the bundled assemblers that
build a disassembly without Docker, and the MAME launch plans the Studio runs.

Disassembling a ROM is the job of
[Retrolarium Disassembler](https://github.com/Retrolarium/retrolarium-disassembler)
(`rldisasm`); the kit builds, verifies and launches the projects it produces
(`rltool project ...`). Both build on `retrolarium-core`.

## Layout

```text
src/retrolarium_kit/
  cli/        rltool: a module per command group; commands import what they run
  core/       game profiles (profiles/), authoring primitives, the generated-
              project runner with its bundled assemblers (toolchains/) and
              OpenDiskSys (opendisksys/), MIDI, the shared linter (lint/)
  platform/   console codecs: nes/ (the resource bundle, CHR, palettes,
              nametables, text, PPU packets, metatiles, APU effects) and fds/
  games/      a package per game: smb, pacman, castle_excellent, solomon, flicky,
              doraemon, alien_soldier; catalog.py dispatches operations and
              nes_codecs.py registers their NES codecs with the platform
  studio/     the .rlj project (project.py), create.py, playtest.py, demo.py,
              audio.py, repository.py and the MAME Lua scripts (runtime/)
  schemas/    JSON schemas of the published documents
tests/        python tools/test.py --suite python: games/<game>/,
              platforms/{nes,fds}/, core/, studio/, cli/ like the package;
              oracles/ holds manual MAME checks run one by one
tools/        maintenance scripts (OpenDiskSys update, SMB demo MIDI)
docs/         authoring contracts
```

Layers import downwards only: core, platform, games, studio, cli. A game never
imports another game, and the platform reaches game codecs only through the
registry (`tests/test_layers.py` checks all of it). The kit does not import the
disassembler.

## Studio project contract

The Kit owns parsing and validation of the hand-editable `.rlj` TOML entrypoint
used by the GUI. A game profile supplies an initial template, but the created
project explicitly owns which editor tabs exist, their order, groups, documents,
codecs, capabilities and dependencies. The retired `.retrolarium` suffix is
intentionally rejected rather than treated as a compatibility alias.

Generated NES resource workspaces declare `Screens -> Graphics/Palettes` and
`Graphics -> Palettes` dependencies explicitly. The GUI uses this graph for
immediate preview invalidation; hand-edited projects may change these edges, and
the Kit rejects missing nodes, disabled dependencies and cycles.

```powershell
rldisasm preset path\to\recognized-game.nes `
  --output build\game-studio
rltool studio create path\to\manifest.json `
  --output path\to\game.rlj `
  --id game --name "Game" --profile game.v1 `
  --resource-document path\to\documents\resources.json
rltool studio inspect path\to\game.rlj
rltool studio validate path\to\game.rlj
rltool studio build path\to\game.rlj
rltool studio verify path\to\game.rlj
rltool studio launch-plan path\to\game.rlj `
  --mame path\to\mame.exe
```

`rldisasm preset` is the normal entry point for Castle Excellent / Castlequest,
Super Mario Bros. 1, and Pac-Man. It recognizes the exact supported revision,
creates a full disassembly and typed resource bundle in a temporary sibling
directory, writes the configured Studio project, and by default builds and
verifies its first artifact. The requested output directory appears only after
the whole workflow succeeds. For a previously edited Castle Excellent levels
JSON, pass `--legacy-castle-excellent-levels path\to\old-levels.json` to
`rldisasm preset`; the Kit validates it against the selected ROM and copies it
into the new multi-document Studio before building. The original JSON and ROM
are never changed. Use `--no-build` only for diagnostics; the GUI always
requests the complete verified workflow.

`inspect` emits the versioned normalized JSON projection consumed by the Qt
application. Project references must be portable forward-slash paths beneath the
project root; absolute paths, `..`, symlink escapes, unknown editor contracts
and dependency cycles are rejected. Operation values are registered identifiers
rather than executable command strings. The resource document is optional; a
recognized `retrolarium.nes-resource-bundle` materializes ordered Screens,
Graphics, Palettes and Code & Analysis tabs with their actual codecs and
capabilities. A recognized `retrolarium.castle-excellent-levels` document
creates a Rooms & Objects tab backed by the same fixed-arena writer. Omitting
the resource document creates the minimal Code-only workspace.

`studio build` first rebuilds the generated source and requires its normal exact
verification to pass. It then applies the current typed authoring document
transactionally and writes the edited image plus a hash-bound build report below
the Studio project's ignored `build/` directory. `studio verify` rejects a stale
artifact whenever the TOML project, a referenced document or the output image
has changed. `launch-plan` accepts only a current artifact and returns the same
allowlisted MAME process specification used by the Qt shell; `studio launch`
provides the bounded headless form for automation.

## Game repositories

A game's disassembly repository opens through the `<game>.rlj` descriptor at its
root (schema `retrolarium.game`): the game, its kit profile, the revisions the
repository builds with the ROM file each expects, and the asset split if the
build needs one. The descriptor carries nothing else. The studio views of a
profile are the kit's (`studio/views/<profile>.toml`), so every repository of a
game shares them instead of keeping a copy.

```powershell
rltool studio revisions path\to\game.rlj
rltool studio prepare path\to\game.rlj --revision <id> [--rom path\to\rom]
rltool studio reset path\to\game.rlj --revision <id>
rltool studio check path\to\game.rlj [--revision <id>] [--present] [--release manifest.json]
```

`prepare` keeps edits in `hack/<revision>/` and writes the generated project and
builds into `.retrolarium/<revision>/`; the repository ignores both. `check` is
what a repository's `make studio-check` runs: in a throwaway copy of the
repository it prepares every revision from its ROM, validates it, builds it with
no edits and compares the result with that ROM byte for byte. It needs every
revision's ROM at the root and never touches the user's folders; with
`--present` it checks the revisions whose ROM is there and reports the others as
skipped (a check that finds none fails).

With `--release`, `check` also checks the `retrolarium` section of the
repository's 3.0 or 3.x release manifest: the descriptor, profile and revisions
it names, the editors it lists as removed, and the kit version it pins, which
must be the running kit's unless the manifest is still in `development`.

### Games and source snapshots

| Game                            | Recognized ROM revisions                                                              | Editing model        |
| ------------------------------- | ------------------------------------------------------------------------------------- | -------------------- |
| Super Mario Bros.               | Japan/USA, Europe, PlayChoice-10, Vs., FDS, All Night Nippon, Super Mario Bros. 2 FDS | ROM-backed documents |
| Castle Excellent / Castlequest  | Japan HSP-05, USA                                                                     | ROM-backed documents |
| Solomon's Key / Solomon no Kagi | USA, Europe, Japan                                                                    | ROM-backed documents |
| Pac-Man                         | Japan v1.0, v1.1                                                                      | ROM-backed documents |
| Doraemon                        | Japan original                                                                        | ROM-backed documents |
| Flicky                          | USA/Europe                                                                            | Source snapshot      |
| Alien Soldier                   | Japan, Europe                                                                         | Source snapshot      |

ROM-backed projects keep documents in `hack/<revision>/`. Source-built games
keep documents inside `.retrolarium/<revision>/`, together with a source
snapshot and its original documents. Flicky uses `flicky-source` and the
`flicky-content` project mode. Alien Soldier uses `alien-soldier-source` and the
`source` mode. Both rebuild the edited source with the kit's assembler.

For a local Alien Soldier checkout, prepare each version independently:

```powershell
rltool studio prepare path/to/alien_soldier.rlj --revision jp --rom path/to/japanese-rom.bin
rltool studio prepare path/to/alien_soldier.rlj --revision eu --rom path/to/european-rom.bin
```

Each answer names the generated revision project. Pass that project's `.rlj` to
`rltool studio build` or open it in Studio. Its views cover levels and objects,
graphics, palettes, animations, screens, text, sound and demos. Payloads retain
their allocated capacities. The game's
[editing guide](https://github.com/RetrolariumLab/alien_soldier_src/blob/main/docs/retrolarium.md)
describes the individual limits and verified previews.

### Downloaded repositories

Open ROM downloads the game's repository as the archive of the commit that
`core/profiles/repositories.json` pins (`commit`; `ref` only names the release
tag or branch it was taken from) and accepts it only when the digest of its
content matches the catalog's `content_sha256`: SHA-256 over one line
`<path>\0<sha256 of the file>\n` per regular file, sorted by POSIX path
(`core/repository_trust.py`, `tree_digest`). Archives with links, more than
20,000 entries or more than 2 GiB unpacked are refused. A verified download gets
a record, `games/<repository>/.retrolarium-verified`, naming the commit, the
content digest and the hash of every file Python could run there (all of
`scripts/`, every `.py`, `.pyw`, `.pyd`, `.so` and `.pth` file).

You edit files of a downloaded repository, so its whole content is checked only
when it is downloaded. Before the kit runs one of its scripts (a descriptor's
split, check or build command, a Flicky snapshot's `scripts/run.py`), it checks
that every such file is one the record lists, unchanged; a changed or added
script stops the command. A repository the kit did not download and verify (a
checkout of your own, a download of an older kit that the pinned commit no
longer matches, a test archive) runs no script unless
`RETROLARIUM_UNVERIFIED_GAMES=1` says you accept unverified game repositories.
The same variable lets a `--source` or `RETROLARIUM_GAMES_SOURCE` archive whose
content differs from the catalog be used, unverified, for development; the
default download is never accepted unverified.

#### Consent before a repository's scripts run

Even a verified repository's scripts run only with your consent, given per
content digest. `RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS` holds the
`content_sha256` values you agreed to, separated by commas or white space, or
`all`; the global flag `rltool --allow-repository-scripts` (accepted anywhere on
the command line) stands for `all` for that one command. An unverified
repository (`RETROLARIUM_UNVERIFIED_GAMES=1`) has no digest to agree to, so its
scripts need `all`.

Without consent the command stops before any script runs, exits with code 3 and
prints exactly one JSON object on stdout (the reason goes to stderr):

```json
{
  "schema": "retrolarium.consent-needed",
  "schema_version": 1,
  "repository": "flicky_src",
  "commit": "<40 hex digits>",
  "content_sha256": "<64 hex digits>",
  "url": "https://github.com/RetrolariumLab/flicky_src/tree/<commit>"
}
```

The Studio shows the repository and the link, remembers your answer for that
repository and digest, and runs the command again with the digest in
`RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS`. Of the catalog's games only Flicky's
build runs repository scripts today (its snapshot's `scripts/run.py`); the
others split with the kit's own assets manifest. Older descriptors with a split
`command` and `check` run them on `prepare` only: `studio revisions` never runs
the check, and reports the split `ok` from `.retrolarium/split-ok.json`, which
records the ROM, the scripts' digest and the kit version of the last check or
split that succeeded.

Every repository script and build step has 15 minutes (`SCRIPT_TIMEOUT`,
`BUILD_TIMEOUT`); the Studio waits 20 minutes for the kit as a backstop. The
Flicky oracles under `tests/oracles/flicky` build local checkouts, so run them
with `RETROLARIUM_UNVERIFIED_GAMES=1` and
`RETROLARIUM_ALLOW_REPOSITORY_SCRIPTS=all`.

To move a game to a newer release, change its `ref` and run
`python tools/pin_game_repositories.py`: it resolves each ref to its commit
through the GitHub API, downloads that archive, computes its content digest and
writes both into the catalog (`--check` only reports). Run
`python tools/check_game_repositories.py` before a release. It downloads each
configured archive into a temporary directory, loads its descriptor and compares
its ROM identities with the catalog. Both need internet access and remove their
temporary downloads when they finish.

### The game's names

The names inside a game (its characters, enemies, items, places) and the lists
that carry them (an animation's frames, the source files a codec reads) are the
game's: its repository keeps them in `config/names.json`, in English, and the
kit and the studio read them when a project opens (`core/game_names.py`). The
kit keeps none of them; where one is missing, numbers stand in. The file's
`guard` list feeds the core's name guard, which `tests/test_game_names.py` runs
over this repository, together with its check for literal tables
(`name_guard_tables.txt` lists the files that may hold one, and why).

### The game's profile

Where a game's resources lie in each revision's image, which codec reads each
one, and the revisions' hashes are knowledge about the game, so its repository
keeps them in `config/game_profile.json` (`retrolarium.game-profile` v1) and the
kit packages none. The kit reads the profile when it opens the descriptor or a
studio project made from it, and checks that it is the profile the descriptor
names. Open ROM recognizes a dump before any repository is there from
`core/profiles/repositories.json`, which lists each game's repository and the
SHA-256 of its revisions.

The places of the tables an `nes.byte-tables.v1` resource reads come from the
game's builds: its `config/profile_tables.json` names them by label, and
`rltool profile tables` reads the builds' ld65 debug files and prints each
revision's offsets. `rltool profile tables --check` compares them with the
profile instead and fails where they differ; a game's `make test` runs it after
building its revisions.

A Disk System game's build writes its programs into the side its ROM gives:
`rltool fds write --disk <side> --file <ids>=<built file> ... --output <side>`
lays each built file over the data of the disk files it names, in order, and
refuses one that does not fill them exactly.

## Record tables

Many games keep small fixed tables in PRG: a stat per enemy, an offset per
weapon direction, a flag per room. A profile describes such a table once as a
`nes.record-table.v1` resource: its record count and labels, and one or more
tables at file offsets, each holding some of the fields record by record or
field by field (`"columns": true`). A field is a byte with an optional range,
signed reading, named choices (with `names`, the studio shows the game's names
for them), a `none` byte shown as null, `locked` records that stay as they are
(the byte is code or another table's), and `unique` values (with as many choices
as records, the records share the choices out). Two tables may share a byte; an
edit that gives it two values is refused. The document repeats the field
descriptions and record labels, so the studio's generic table view needs nothing
else. Doraemon's object tables and its four sound drivers' effect routing use
it.

## Bundled compiler suites

The kit carries the assemblers the game repositories build with: cc65's `ca65`
and `ld65` for the NES, the AS macro assembler and `p2bin` for the Mega Drive,
for Windows x86-64 and Linux x86-64. Nothing is downloaded at first use: the
commands copy hash-verified files into a writable cache.

The assemblers are built here, not taken from elsewhere:
`tools/build_toolchains.ps1` builds them in Docker (`tools/toolchains/`) from
the AS source archive in `core/toolchains/sources` and cc65 and p2bin at pinned
commits, into `core/toolchains/payloads`. The build is reproducible, and
`core/toolchains/manifest.json` pins every file's size and SHA-256, so a
checkout builds them once and the studio's package ships them; users need
nothing installed. After a deliberate change (a compiler image or source
commit), `python tools/toolchains/write_manifest.py` writes the new digests.

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_toolchains.ps1
rltool build-tools status
rltool build-tools path nes ca65
rltool build-tools path megadrive asl
```

Python build integrations use the public API instead of a repository-local
`bin/` directory:

```python
from retrolarium_kit.core.build_toolchains import materialize_build_toolchain

nes = materialize_build_toolchain("nes")
ca65 = nes.command("ca65")
ld65 = nes.command("ld65")

sega = materialize_build_toolchain("megadrive")
asl = sega.command("asl")
p2bin = sega.command("p2bin")
environment = sega.process_environment()
```

AS is GPL-2.0-or-later: its source snapshot ships beside the binaries built from
it, with the license. cc65 (zlib) and p2bin (ISC) notices ship as well.

The Mega Drive compressions (Nemesis, Enigma) are the core's C++, which the kit
runs as `rlcore` (Flicky's graphics need it). The core looks for it in this
order: `RETROLARIUM_CORE` (the Studio names the `rlcore` it ships there), the
one inside the core's wheel, then `PATH`. A source checkout builds it in
`retrolarium-core` (`cmake -S . -B build` there, then
`cmake --build build --target rlcore`) and either names it in `RETROLARIUM_CORE`
or sets `RETROLARIUM_CORE_DEV=1`, which alone lets the core use the checkout's
`build/rlcore`. The core refuses an `rlcore` whose version is not its own.

## Shared linter

Every game repository keeps one house style, checked and applied by the kit: the
ca65 layout of the NES disassemblies, the AS layout of the Mega Drive ones and a
text-format contract for the rest of the tree (LF endings, no trailing spaces,
one final newline, valid JSON). A game configures its assembler dialect, source
folders and text exclusions in `config/lint.json`. Checks about one game's own
source stay in its repository.

```json
{
  "schema": "retrolarium.lint",
  "schema_version": 1,
  "assembly": {"dialect": "ca65", "paths": ["src"]},
  "text": {"json": "valid", "skip": ["assets/generated"]}
}
```

```powershell
rltool lint --project .
rltool lint --project . --fix
rltool lint --project . --fix --only assembly src/game/new_module.asm
```

`--fix` changes only whitespace and comment text, so a formatted source
assembles to the same bytes. The text contract reads the files Git sees and
always skips `.git`, `build`, `.retrolarium` and `hack`. `"json": "indent"` also
lays JSON out with two-space indentation, for games that write their JSON by
machine.

Set `"label_length": true` in the `assembly` object to enforce a maximum of 40
characters per label in `.s`, `.inc` and `.asm` files. The check includes local
labels and AS named declaration fields such as `equ` and `macro`. It counts the
identifier as written, including a local prefix such as `.` or `@`, and excludes
the colon. `--fix` never renames a symbol.

An optional `config/asm-label-exceptions.json` allows a specific spelling in a
specific source file. Use a project-relative path with forward slashes and give
each exception a reason.

```json
{
  "src/system/interface.s": {
    "LegacyExternalInterfaceNameSharedWithOtherTools": "External interface with independently maintained consumers"
  }
}
```

Projects with a custom assembly formatter can run
`rltool lint --project . --only labels` from `make lint` to check their original
sources directly. The rule is disabled until the project enables it.

`rltool hooks install --project .` (a game's `make hooks`) installs the core's
Git hooks: every commit and push is checked for stop words, and every commit
runs the game's `make lint` first. The hooks remember this kit as `RLTOOL`, so
they work without `rltool` on `PATH`.

## Castle Excellent / Castlequest authoring

The first profile-driven authoring slice recognizes both supported revisions by
exact hash and validates the complete room/object layout before exposing an
editable document. The source image is immutable; `build` writes a new image and
may also write a machine-readable capacity/diff report.

The profiles live in the game repositories (`config/game_profile.json`): a
command uses the profile of the repository the current folder or the files it
names lie in, so the commands below run from the game's repository (or take
`--profile`).

```powershell
rltool game profiles
rltool game inspect "Castle Excellent (J).nes"
rltool game extract "Castle Excellent (J).nes" `
  --output build/castle_excellent/levels.json
rltool game validate "Castle Excellent (J).nes" build/castle_excellent/levels.json
rltool game build "Castle Excellent (J).nes" build/castle_excellent/levels.json `
  --output build/castle_excellent/edited.nes `
  --report build/castle_excellent/build-report.json
rltool game launch-plan "Castle Excellent (J).nes" build/castle_excellent/edited.nes `
  --mame path/to/mame.exe
rltool game launch "Castle Excellent (J).nes" build/castle_excellent/edited.nes `
  --mame path/to/mame.exe `
  --output build/castle_excellent/mame-runs --seconds 2
```

Game/revision identities, resource codecs, addresses, arenas and capacity rules
live in the game's profile, `config/game_profile.json` in
`castle_excellent_src`; the kit packages no game profiles. `--profile` selects
the profile file, parsed with the same strict v1 contract. The shipped codec
repacks the two room arenas, room pointers, terminated object stream, object
count/state offsets and mutable-cell state offsets as one transactional
`BuildPlan`. Zero-edit builds of both known revisions are byte-identical.

The extracted document also contains a profile-bound
`castle-excellent.room-rendering.v1` snapshot: CHR bank 0, the 32-byte gameplay
palette, 100 left/right cell-tile mappings, the native attribute tables, the
61-entry encoded-to-runtime object type table and the representative metasprite
descriptors. Schema v1 keeps this payload read-only and verifies it against the
source image during build, while allowing Qt to render exact background and 8×16
sprite pixels without reopening or interpreting the ROM itself.

`game launch-plan` produces the same allowlisted `retrolarium.process-spec`
consumed by the Qt shell. `game launch` is its bounded headless verification
form: it saves logs, screenshots and a versioned run result below the explicit
output directory. Both commands recognize the immutable source revision and
validate the edited image before MAME can be started.

The validate/build report includes the edited-byte count and used/capacity/free
byte totals for both room banks and the object stream. The Qt bridge validates
this arithmetic before presenting a concise summary in the common diagnostics
dock.

The generic Python layer (`Address`, `AddressRange`, `BinaryReader`,
`BinaryWriter`, `GameProfile`, `ResourceSpec`, `ResourceDocument`, `ChangeSet`,
`BuildPlan` and `Registry`) is game-independent. The Castle Excellent adapter is
selected by codec id rather than game-specific CLI branching.

Retrolarium Studio calls the same commands with explicit `QProcess` arguments.
Its Castle Excellent editor works on rooms, cells and objects with native CHR
and palette previews; it delegates all codec and capacity validation back to
this package and plays the edited ROM through the same launch plan.

## Open HonkeyPong NES resources

The profile in `tests/fixtures/game_honkeypong.json` exercises a different
authoring family on the MIT-licensed HonkeyPong fixture.
`nes.resource-bundle.v1` exposes the title nametable, three palette blocks and
all 512 CHR-ROM tiles as typed JSON, then uses the same transactional
`BuildPlan` and public game commands. It supports zero-edit exact rebuilds,
edited builds and the common MAME launch path without a HonkeyPong-specific CLI
branch.

```powershell
rltool game inspect ../retrolarium-disassembler/fixtures/nes/bin/HonkeyPong.nes `
  --profile tests/fixtures/game_honkeypong.json
rltool game extract ../retrolarium-disassembler/fixtures/nes/bin/HonkeyPong.nes `
  --profile tests/fixtures/game_honkeypong.json --output build/honkeypong/resources.json
rltool game build ../retrolarium-disassembler/fixtures/nes/bin/HonkeyPong.nes `
  build/honkeypong/resources.json --profile tests/fixtures/game_honkeypong.json `
  --output build/honkeypong/edited.nes
```

See [`platform/nes/resources.md`](docs/platform/nes/resources.md).

## Pac-Man maze authoring

The `pacman.v1` profile (in `pacman_src`) recognizes the two verified
layout-compatible Japan v1.0/v1.1 revisions. `game extract` produces one
`retrolarium.nes-resource-bundle` containing the 27x22 maze, 23x6 title logo, 14
fixed-width messages, five main palette contexts, complete CHR bank and fixed
actor mappings; no ROM bytes are distributed with the Kit. `pacman.maze-rle.v1`
preserves the source's 416 token bytes for a byte-exact zero-edit build and
deterministically repacks changed tiles without moving later data.

The codec rejects invalid geometry, tile IDs, pellet/power-pellet counts,
blocked actor spawns or tunnels, broken door placement, unexpected path
components and RLE overflow. A Studio project generated from the bundle owns
Maze, Graphics, Palettes, Animations & Mappings and Code tabs, with Maze and
Animations depending on Graphics for immediate preview refresh.
`pacman.palette-table.v1` exposes the title, attract, round-gameplay,
intermission-sprite and fruit-by-stage tables. Its declared mirror pairs are
validated and written together, preventing a GUI edit from breaking the NES
universal-background slots. Frightened/normal color command copies are
intentionally deferred until the bundle supports replicated ranges.
`pacman.actor-mapping.v1` round-trips the original 624-byte table as 64 standard
and 13 alternate four-quadrant frames, validates OAM attribute bits and keeps
the shared 16x16 offset layout read-only. See
[`games/pacman/maze.md`](docs/games/pacman/maze.md).

The title logo reuses `nes.nametable.v1` as a tile-only 23x6 region. Both
verified revisions rebuild exactly, and the common Screens editor disables its
palette brush when a region has no attribute bytes. The addressed PPU packets,
HUD/intermission regions are not bundled yet. `nes.fixed-text-fields.v1`
projects 14 disjoint title, attract and pause ranges into one Text resource; the
profile owns the glyph map, case folding, labels, widths and addresses. See
[`platform/nes/text.md`](docs/platform/nes/text.md).

## Super Mario Bros. authoring

The `smb1.v1` profile (in `smb1_src`) recognizes the verified byte-identical JU
build from the open `smb1_src` reconstruction. `game extract` emits a
12-resource bundle containing all 36 course routes, nine player-physics tables,
34 level areas, metatiles, palette packets and player palettes, five screen
regions, seven fixed text fields, 26 player frames, the main APU music bank and
effect envelope, and the complete CHR bank. The generated Studio workspace owns
World, Levels, Screens, Music & Sounds, Text, Graphics, Metatiles, Palettes,
Player Frames and Code & Analysis tabs. These editors share undo/redo, restore
and atomic save. The zero-edit disassemble/extract/build/verify path is
byte-identical. See [`games/smb/world.md`](docs/games/smb/world.md). ANN and
SMB2 Japan additionally expose their ending-bank FDS waveforms and volume
programs through a dedicated `audio.synthesis` Studio tab; see
[`platform/nes/fds_synthesis.md`](docs/platform/nes/fds_synthesis.md).

## Movies in MAME

A game checks itself by playing recorded movies in MAME: FCEUX `.fm2` and Gens
`.gmv` recordings play as they are, with no conversion step, and MAME needs no
window or sound. Next to each recording a movie check (`retrolarium.movie-check`
v1) names the RAM cells to read, the frames to read them after (`end` is the
last frame) and the values the original ROM gives:

```json
{
  "schema": "retrolarium.movie-check",
  "schema_version": 1,
  "movie": "pacman_j_longplay.fm2",
  "cells": {"stage": {"address": "0x0068"}, "score": {"address": "0x0070", "length": 7}},
  "frames": [30000, 60000, "end"],
  "expected": {}
}
```

```powershell
rltool movie golden movies\pacman_j_longplay.check.json --rom original.nes --mame mame\consoles.exe
rltool movie check movies\smb1_any_percent.check.json movies\smb1_longplay.check.json --rom build\relocated\smb.nes
rltool movie play movies\smb1_any_percent.check.json --rom build\relocated\smb.nes
```

`play` is `check` you can watch: MAME opens a window with sound at the game's
speed, stays open when the movie ends, and the cells are compared once you close
it. `rltool movie mame <path>` remembers the MAME to use (in
`%LOCALAPPDATA%\Retrolarium\settings.json`, or `~/.config/retrolarium/`), so
neither `--mame` nor `RETROLARIUM_MAME` is needed after it.

`golden` plays the movie on the original ROM and writes `expected`; `check`
plays it on a build and fails on any cell that differs. Both take several checks
and play them in the order given; `check` stops at the first that fails, so a
game names its TAS first and its longplay after it. A cell may carry a
`tolerance` (with `endian`, `big` or `little`): on the NES a moved routine can
cross a page boundary and cost the odd lag frame, so a position may trail by a
few pixels without anything being wrong. `RETROLARIUM_MAME` names MAME when
`--mame` does not. Frames are counted as the recording emulator counts them;
MAME starts a frame earlier and reads the pads a frame later, which the kit
evens out (measured on whole longplays: equal RAM at every frame checked). An
FCEUX movie also starts from FCEUX's power-on RAM (`00 00 00 00 FF FF FF FF`
repeated), which the kit's MAME plugin `rlpoweron` writes before the first
instruction. A game whose work per frame depends on exact CPU timing (Solomon's
Key runs a cooperative scheduler in its main loop) can still lag differently in
MAME than in FCEUX, and a frame-exact TAS of it does not stay in sync.
`rltool movie convert` writes a recording as a `.rlmovie` file, one line per run
of equal frames.

Every game runs its movies the same way: `make build-relocated` writes a copy of
the source in which code and data move (bytes inserted between modules and
absorbed by padding before the fixed parts) and builds it into
`build/relocated/`, and `make test-full` plays the game's movies on that build,
TAS first, then the longplay, then attract-mode or demo checks.

## Other commands

```powershell
python rltool.py project validate `
  build/game-disassembly/manifest.json
python rltool.py project build `
  build/game-disassembly/manifest.json
python rltool.py project verify `
  build/game-disassembly/manifest.json
python rltool.py project launch `
  build/game-disassembly/manifest.json `
  --mame path/to/mame.exe --seconds 2
python tools/test.py --suite python
```

Generated NES/SNES projects use the bundled `ca65-ld65-v1` recipe, and Mega
Drive projects use `asl-p2bin-v1`. `project build` therefore needs neither
Docker nor a local compiler directory; pass `--container IMAGE` only when an
explicit compatibility build is wanted (`--container default` runs the image the
manifest names as `default_container`). The kit never starts Docker on its own:
a manifest without a bundled recipe fails with the `--container` to add.

Build or install the kit from a checkout of this repository. A wheel carries the
assembler payloads, which are not tracked: build them first with
`tools/build_toolchains.ps1` (Windows) or `tools/build_toolchains.sh` (Linux,
macOS), and check them with `python tools/check_toolchain_payloads.py`, which
fails when any file the manifest pins is missing or differs
([docs/toolchains.md](docs/toolchains.md)):

```powershell
python -m pip install .
rltool --version

uv build --wheel --out-dir build/dist .
```

An editable development install uses `python -m pip install -e .`. All forms
expose the `rltool` entry point.

## Checks before a commit

`make hooks`, once per clone, makes Git check every commit and push for stop
words and run `make lint` and `make format-check` first (see Retrolarium Core's
README, "Stop words and Git hooks"). The hooks and the Makefile use the
`retrolarium-core` checkout beside this one.

## Trademarks

The games are named only to say which ones Retrolarium works with. Their titles
and characters are trademarks of their owners, who have not made or endorsed
Retrolarium. Retrolarium does not include the games: you need your own copy of
each.

## License

Retrolarium Kit is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version (`GPL-3.0-or-later`, text in `LICENSE`). It comes without any warranty.
The bundled toolchains keep their own licenses
(`src/retrolarium_kit/core/toolchains/licenses`).

## Similar assembly routines

Use `rltool asm similar` to compare 6502, 65816 and 68000 routine blocks.
[The source similarity guide](docs/assembly_similarity.md) describes scores,
platforms, path filters and structured reports.

## Portable test runner

The same entrypoint runs on Windows, Linux and macOS with Python 3.11 or later.
Clone the current `retrolarium-core` beside the other products (or install a
Core that includes `retrolarium_core.testing`). The runner selects the
checkout's sources explicitly. No PowerShell or Bash wrapper is needed. Python
suites use Git for repository fixtures. Native suites additionally need CMake
and the chosen build tool, such as MSBuild, Ninja or Make. The CI matrix covers
Windows and Linux.

```text
python tools/test.py
python tools/test.py --suite python
python tools/test.py --suite checks
python tools/test.py --suite checks --format --history
```

The default runs all tests this product has. Core and Studio also build and run
their native tests with CMake and a host C++ compiler. The Kit and Disassembler
have Python suites. `--suite python` needs no compiler or Qt. Repository guards
and formatting are separate opt-in checks. The test runner explicitly excludes
the PowerShell formatter, so it never probes or starts PowerShell. The other
formatters retain their own language-specific tools; the separate `make format`
command still supports PowerShell files. A private stop-word list comes from
`RETROLARIUM_STOPWORDS`, an ancestor `.stopwords`, or CI's `STOPWORDS_LIST`. A
missing unconfigured list is reported as skipped; a configured missing file
fails. `--history` still checks historical objects.

Native test failures and timeouts fail the command. Missing game fixtures and a
test's exit code 77 count as skips, never passes. Missing executables or tracked
repository fixtures are failures. `--game-sources PATH` selects the prepared
game repositories; paths with spaces work without shell quoting inside the
runner. `--jobs`, `--config` and `--build-dir` control CMake. Windows, POSIX and
filesystem-specific assertions still run on their own platforms; the common
runner does not pretend another host has those APIs.

## Synthetic SMB tests and local ROM corpora

SMB renderer tests use original synthetic tables and scenes generated by
`tools/smb_test_corpus.py`. They need no ROM or game checkout. The `synthetic`
subcommand also reproduces Studio's public SMB fixtures:

```text
python tools/smb_test_corpus.py synthetic --output ../retrolarium-studio/tests/fixtures/smb
```

For a separate local comparison, `rom --rom PATH --profile PATH` verifies your
SMB1 JU ROM and exports all 34 areas, screen/text resources and music timelines
to the ignored `build/smb-rom-corpus/` directory. It refuses output outside an
ignored build subdirectory or into tracked files. Never commit or distribute
these ROM-derived corpora. From Studio, run `tools/test_smb_rom.py` with its
`--bin` and `--corpus` arguments; Studio's `docs/testing_smb.md` describes the
complete workflow. Public CI uses only the synthetic mode.

## Core revision in CI

CI checks out the full Core commit recorded in `.github/core-ref`, so rerunning
an older revision uses the same dependency. `python tools/core_pin.py` validates
and prints the pin. To update it, test the intended Core commit with this
checkout, put its full lowercase SHA in that file, and commit the update.
Publish the Core commit before publishing a dependent pin update.

The make entrypoints use `python` on Windows and `python3` elsewhere;
`make PYTHON=...` selects an explicit interpreter.
