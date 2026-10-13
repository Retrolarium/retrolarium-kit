# Retrolarium SMB Level Authoring Contract

Status: implemented editable vertical slice for the verified JU profile

Codec: `smb.levels.v1`

## Scope

The `smb1.v1` profile (in `smb1_src`) exposes all 34 procedural areas from the
byte-identical open `smb1_src` JU build. Area-object and enemy streams remain
parallel named records rather than being flattened into a tilemap. No ROM or
extracted asset is distributed with the package.

| Range         | File offset | Size | Typed content                               |
| ------------- | ----------: | ---: | ------------------------------------------- |
| Enemy streams |    `0x1D80` | 1087 | enemies, entrances, page commands and flags |
| Area streams  |    `0x21BF` | 3373 | headers and procedural object commands      |

The profile owns stream names, order and the separate fixed capacity of every
area/enemy pair. Both non-contiguous ranges participate in one transactional
build plan.

## Exact stream semantics

An area header exposes timer, entrance, foreground/color, area style, background
scenery and terrain controls with their native bit widths. Each area object
exposes column, row, page-advance and the seven-bit object control.

Enemy records are discriminated by their native row nibble:

- rows `0..13` are enemies;
- row `14` is a three-byte entrance with destination world/page;
- row `15` is a page command.

The codec preserves explicit suffix padding and models a terminator shared with
the next stream as `implicit_terminator`. In the JU build this is required by
the full-capacity `ground_9` enemy stream. An implicit stream must consume its
capacity exactly; an explicit stream may grow only up to its profile capacity.

## Studio integration

Resource-driven project generation maps this codec to `level.procedural` and a
`Levels` tab; there is no game-ID branch in the tab host or project format. The
Qt editor provides:

- all-area selection and native header fields;
- typed area-object and enemy/entrance/page tables;
- add, update and delete actions guarded by stream capacity;
- page-aware horizontal semantic preview and a day/night view switch;
- byte-budget status, undo/redo, undoable restore and atomic save.

The preview currently visualizes engine page placement, terrain control,
objects, enemies and entrances. Reconstructing the exact metatile/background
rendering and selection-to-source navigation remains part of the wider SMB
parity stage.

## Verification

Synthetic tests cover explicit and implicit terminators, preserved padding, page
positioning, invalid record kinds, capacity overflow and transactional edits to
both ROM ranges. The real JU pipeline reports 34 levels, 1635 area objects and
502 enemy records. Its zero-edit Studio build has `changed_bytes = 0`, passes
project validation/verification and Qt project smoke, and retains SHA-256
`f61548fdf1670cffefcc4f0b7bdcdd9eaba0c226e3b74f8666071496988248de`.
