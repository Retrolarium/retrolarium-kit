# Pac-Man NES maze, screen, text, palette and actor authoring contract

Status: implemented first Pac-Man Studio vertical slice.

## Public boundary

The packaged `pacman.v1` game profile recognizes only the Japan v1.0 and v1.1
revisions whose resource locations were independently verified. Recognition is
by complete image size, SHA-1 and SHA-256; the package contains metadata and
code only, never ROM bytes.

The profile selects the existing `nes.resource-bundle.v1` authoring adapter and
composes ten fixed resources in one `retrolarium.nes-resource-bundle` document:

- `maze`, kind `level`, codec `pacman.maze-rle.v1`;
- `title-logo`, kind `screen`, codec `nes.nametable.v1`;
- `game-text`, kind `text`, codec `nes.fixed-text-fields.v1`;
- five palette resources, kind `palette`, codec `pacman.palette-table.v1`;
- `character-graphics`, kind `graphics`, codec `nes.chr-2bpp.v1`;
- `actor-frames`, kind `animation`, codec `pacman.actor-mapping.v1`.

This is deliberately not a Pac-Man CLI branch. The normal
`game inspect/extract/validate/build` and `studio create/validate/build/verify`
operations resolve it through the profile and codec registries.

## Maze representation

The authored projection is a 27-row by 22-column matrix of tile IDs `$00..$3F`.
Each source byte stores a run of one to four tiles in its high two bits and a
tile ID in its low six bits. Runs may not cross row boundaries. The resource
has a fixed 416-byte arena.

The document retains the extracted `tokens` alongside `tiles`. When they still
describe the same grid, the writer emits the original token bytes exactly.
This preserves a byte-identical zero-edit build even when several equivalent
RLE tokenizations exist. If the grid changed, the writer greedily creates
row-bounded runs and deterministically splits them until the same 416-byte
arena is filled. The decoded grid is the authoring source of truth.

## Runtime-sensitive validation

Validation and GUI save enforce the assumptions used by the original engine:

- exactly 192 collectible tiles and four visible power pellets;
- the `$2C` ghost-house door remains at row 11, column 11;
- Pac-Man and ghost spawn cells remain passable;
- both tunnel endpoints on row 13 remain passable;
- the passable map has only the main component and optional six-cell house
  interior;
- the minimum row-bounded encoding fits the 416-byte allocation.

Invalid documents never reach the transactional ROM writer.

## Qt consumer

`level.tile-grid` plus `pacman.maze-rle.v1` selects
`PacmanMazeEditorWidget`. It renders the maze through the CHR resource from the
same shared document and provides 64-tile selection, complete-stroke undo,
redo, right-click eyedropper, restore, original-change borders and semantic
overlays for tunnels, ghost house, door, spawns and power pellets. The generated
Studio TOML declares `maze -> graphics`, so CHR edits refresh the maze preview
without reloading the project.

## Screen representation

The first Screens slice exposes the original fixed-bank 23x6 title logo as a
tile-only `nes.nametable.v1` resource. It has no attribute bytes of its own and
is rendered through the shared CHR and title-palette resources. The common
screen widget therefore disables palette painting for this region while
retaining tile painting, eyedropper, stroke undo, restore and atomic save.

Addressed title/attract packets, title attributes, HUD and intermission visual
tables remain future resources. Game-font messages are kept in a separate Text
tab instead of being mixed into the screen canvas.

## Text representation

`nes.fixed-text-fields.v1` projects 14 disjoint title, attract and pause ranges
into one typed resource. The profile declares every label, stable key, address,
fixed width, glyph/code mapping, padding byte and case-folding policy. The Kit
rejects overlapping profile ranges, altered metadata, overlong strings and
unsupported glyphs before constructing the transactional multi-range build
plan. Existing one-range resource reports remain backward compatible while
this resource reports all 14 ranges explicitly.

`text.fixed` selects the reusable `NesTextEditorWidget`. It provides named
message selection, normalized uppercase input, undo/redo, per-message restore
and atomic shared-document save without a Pac-Man branch in the factory.

## Palette representation

`pacman.palette-table.v1` retains each fixed palette table as NES master-palette
indices. The profile names five runtime contexts: title background, attract
background/sprites, round gameplay, intermission sprites and fruit-by-stage.
The 32-byte attract and gameplay tables declare the four universal-background
mirror pairs required by the PPU layout. Validation rejects divergent mirror
values or a modified mirror contract, and an edit to either side updates both
bytes in the shared document as one undoable action.

`palette.set` plus this codec selects the common `NesPaletteEditorWidget`; no
Pac-Man-specific widget or game-ID branch is involved. The scattered
frightened/normal color command copies are not represented yet: safely editing
them requires a future replicated-range resource rather than pretending they
are one contiguous palette table.

## Actor mapping representation

`pacman.actor-mapping.v1` retains the fixed 624-byte actor block as 64 standard
and 13 alternate frames. Each frame contains four sprite-bank tile IDs and four
OAM attributes. The editor and writer allow palette, priority, horizontal flip
and vertical flip bits and reject the reserved attribute bits. Four shared
signed Y/X offsets must form a 16x16 quadrant layout and remain read-only.

`animation.mapping` plus this codec selects `PacmanActorEditorWidget`. It
renders the selected frame through tiles `$100..$1FF` of the shared CHR
resource, exposes the four quadrant records, and provides undo, redo,
frame-level restore and atomic save. The generated dependency
`animations -> graphics` refreshes the metasprite immediately after a CHR edit.

## Verification

Automated tests cover synthetic fixed-size round trips, exact zero-edit builds,
known balanced edits, semantic rejection, exact palette and actor-table round
trips, mirrored and byte-local edits, C++ document access, widget
undo/restore/save guarding and factory selection. A local ignored Japan v1.0
fixture additionally proves exact Ghidra/ca65 disassembly, extraction, Studio
creation, validation, build, verification and `--studio-smoke` loading. Japan
v1.1 also passes a standalone ten-resource exact rebuild. A known v1.0 edit
changes only the expected byte at file offset `$355`.
