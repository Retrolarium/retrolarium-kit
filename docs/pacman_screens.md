# Pac-Man Screen Authoring Contract

Status: first production codec implemented

Date: 11 September 2026

## Purpose

`pacman.screen-collection.v1` gathers Pac-Man's screen data, which the original
game scatters across PRG ROM, into one typed resource. The same document is
edited by two independent Studio tabs:

- `Screens` edits the tilemap, PPU packets, HUD, pause and intermission data;
- `Text` shows the named fixed-width strings inside the same blocks.

This is not a general NES nametable codec. The layout, sizes and addresses of
the original game come from the profile, and the Python codec stays the only
owner of binary decode and encode.

## Resource document

The resource has `kind = "screen"`, `codec = "pacman.screen-collection.v1"` and
these fields:

| Field                   | Content                                    | Editing                      |
| ----------------------- | ------------------------------------------ | ---------------------------- |
| `title_logo`            | PPU origin, width, height and a 2D tilemap | tiles and PPU address        |
| `title_packets`         | six `{address, bytes}` packets             | address and payload          |
| `title_attributes`      | PPU origin and 24 attribute bytes          | address and bytes            |
| `fixed_blocks`          | menu, player glyph, HUD and pause blocks   | bytes with structural guards |
| `attract_packets`       | ten `{address, bytes}` packets             | address and payload          |
| `intermission_visual`   | cycle tiles and indexes, banner quads      | bytes with allowlists        |
| `read_only_code_tables` | a catalogue of tables embedded in code     | view and analysis only       |
| `encoding`              | glyph map, padding and case folding        | profile metadata             |
| `text_fields`           | key, label, source path, start and width   | a text view of the bytes     |

`text_fields.path` points into the payload of a title or attract packet, or into
a fixed block. Editing a string replaces the matching tile bytes in the screen
resource itself; there is no second, independent copy of the text.

## Profile

The game profile gives:

- the file offsets and fixed sizes of every area;
- the PPU origins of the title logo and attributes;
- the payload size of each title and attract packet;
- the attract pointer table and the expected packet offsets;
- the list of fixed blocks and intermission tables;
- the allowlist of values for the restricted visual indexes;
- the catalogue of read-only code tables;
- the text encoding and the named text fields.

The Pac-Man profile covers Japan v1.0 and Japan v1.1. Revision-specific offsets
may differ, while the normalised document and the editors stay the same.

## Binary semantics

A PPU packet is a big-endian address, a payload and a `$FF` terminator. The
payload cannot contain `$FF`. Packet sizes are fixed in the base codec: that
lets the data change safely without moving the code and tables around it.

The writer returns several non-overlapping spans. The resource bundle applies
them as one transaction and rejects any overlap with other resources. The
attract pointer table is checked on decode but not rewritten, since the packets
do not move.

Structural guards keep:

- a valid PPU address and the final `$FF` of the menu prompt;
- both `$FF` terminators of the pause packets;
- the sizes of every fixed block;
- the allowlist `0, 4, 8` for the intermission cycle pattern indexes;
- the shape and values of the read-only catalogue;
- the fixed width and glyph map of the embedded text fields.

## Editors

Retrolarium Studio's Pac-Man plugin offers a screen asset selector, a visual
grid, a CHR preview of the title logo, a hexadecimal byte inspector, a PPU
address editor for addressed elements, undo/redo, restore and atomic save.

The `Text` tab uses the shared NES text editor. Both editors work on one shared
resource document, so changes show without decoding the ROM again. Whether both
tabs exist is decided by the `.rlj` project file.

## Verification

The required matrix:

- an exact synthetic decode/encode round trip;
- rejection of wrong pointers, packet sizes, `$FF`, allowlists and metadata;
- an embedded text edit with a multi-range build;
- document and editor undo, restore and save round trips;
- a zero-edit extract/build of Pac-Man Japan v1.0 and v1.1 with an exact
  SHA-256.

ROMs, extracted JSON and built ROM files are reproducible local fixtures and are
not added to Git.
