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
              doraemon; catalog.py dispatches profile operations to them and
              nes_codecs.py registers their NES codecs with the platform
  studio/     the .rlj project (project.py), create.py, playtest.py, demo.py,
              audio.py, repository.py and the MAME Lua scripts (runtime/)
  schemas/    JSON schemas of the published documents
tests/        python -m unittest discover -s tests: games/<game>/,
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
rltool studio check path\to\game.rlj [--revision <id>] [--release manifest.json]
```

`prepare` keeps edits in `hack/<revision>/` and writes the generated project and
builds into `.retrolarium/<revision>/`; the repository ignores both. `check` is
what a repository's `make studio-check` runs: in a throwaway copy of the
repository it prepares every revision from its ROM, validates it, builds it with
no edits and compares the result with that ROM byte for byte. It needs every
revision's ROM at the root and never touches the user's folders.

With `--release`, `check` also checks the `retrolarium` section of the
repository's 3.0 or 3.x release manifest: the descriptor, profile and revisions
it names, the editors it lists as removed, and the kit version it pins, which
must be the running kit's unless the manifest is still in `development`.

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

## Shared linter

Every game repository keeps one house style, checked and applied by the kit: the
ca65 layout of the NES disassemblies, the AS layout of the Mega Drive ones and a
text-format contract for the rest of the tree (LF endings, no trailing spaces,
one final newline, valid JSON). A game keeps only `config/lint.json`, which
names its assembler dialect and source folders and the trees the text contract
skips; checks about one game's own source stay in its repository.

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

## Castle Excellent / Castlequest authoring

The first profile-driven authoring slice recognizes both supported revisions by
exact hash and validates the complete room/object layout before exposing an
editable document. The source image is immutable; `build` writes a new image and
may also write a machine-readable capacity/diff report.

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

See [`nes_resources.md`](docs/nes_resources.md).

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
[`pacman_maze.md`](docs/pacman_maze.md).

The title logo reuses `nes.nametable.v1` as a tile-only 23x6 region. Both
verified revisions rebuild exactly, and the common Screens editor disables its
palette brush when a region has no attribute bytes. The addressed PPU packets,
HUD/intermission regions are not bundled yet. `nes.fixed-text-fields.v1`
projects 14 disjoint title, attract and pause ranges into one Text resource; the
profile owns the glyph map, case folding, labels, widths and addresses. See
[`nes_text.md`](docs/nes_text.md).

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
byte-identical. See [`smb_world.md`](docs/smb_world.md). ANN and SMB2 Japan
additionally expose their ending-bank FDS waveforms and volume programs through
a dedicated `audio.synthesis` Studio tab; see
[`fds_synthesis.md`](docs/fds_synthesis.md).

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
python -m unittest discover -s tests
```

Generated NES/SNES projects use the bundled `ca65-ld65-v1` recipe, and Mega
Drive projects use `asl-p2bin-v1`. `project build` therefore needs neither
Docker nor a local compiler directory; pass `--container IMAGE` only when an
explicit compatibility build is wanted.

Build or install the kit from a checkout of this repository:

```powershell
python -m pip install .
rltool --version

uv build --wheel --out-dir build/dist .
```

An editable development install uses `python -m pip install -e .`. All forms
expose the `rltool` entry point.

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
