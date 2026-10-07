# NES fixed-resource authoring contract

Status: the generic Kit adapter and the first open-game profile are implemented
and proven through exact rebuild, a real edit and MAME.

## Purpose

Castle Excellent exercises compressed variable-size rooms, pointer repacking and
coupled object/state tables. A second adapter is required to prove that the
public authoring operations and `GameProfile` are not Castle-specific.

`nes.resource-bundle.v1` covers independently located, fixed-capacity NES
resources. Its first profile is HonkeyPong, an MIT-licensed mapper-0 game built
from the pinned open-source fixture at commit
`f9ab7ad450ffebabec15623aa811a7564f079168`.

The profile recognizes only the 24,592-byte fixture image with SHA-256
`cce7bf2f71eb9e63de0dba15098679dde06a1da9d7553f2319aa60ea57a1193b` and SHA-1
`efdb529788269041c3fd4adf0ce2611ca40e280b`. The ROM is a reproducible ignored
fixture; no ROM bytes are committed as part of the profile.

## Profile evidence

The committed profile is `profiles/game_honkeypong.json`. Its ranges were
cross-checked three ways: Ophis source `.incbin` directives, the generated
`HonkeyPong_Debug.map`, and byte comparison between each upstream asset and the
built ROM.

| Resource           | Codec                  |      File range | Source symbol/asset                        |
| ------------------ | ---------------------- | --------------: | ------------------------------------------ |
| logo palette       | `nes.palette-table.v1` |   `0x03E2+0x20` | `honkeyPal`, repeated 16-byte palette      |
| title palette      | `nes.palette-table.v1` |   `0x0402+0x20` | `titlePal`, repeated 16-byte palette       |
| game palette       | `nes.palette-table.v1` |   `0x0422+0x20` | `gamePal`, background plus sprite palettes |
| title screen       | `nes.nametable.v1`     |  `0x0442+0x400` | `TitleScreen`, `title.nam`                 |
| character graphics | `nes.chr-2bpp.v1`      | `0x4010+0x2000` | iNES CHR bank, `honkeypong.chr`            |

The three palette resources expose NES color values in the range 0–63. The
nametable exposes a 32 by 30 tile matrix and 64 attribute bytes. CHR is decoded
into 512 typed 8 by 8 tiles whose pixel values are 0–3; encoding reconstructs
the two NES bitplanes exactly.

## Document and build boundary

Extraction writes a `retrolarium.nes-resource-bundle` v1 document. The JSON
schema is `schemas/nes-resource-bundle-v1.schema.json`; runtime parsing also
requires the exact ordered resource set, game/revision/source identities,
geometry, lengths and value ranges from the selected profile.

The adapter creates one provenance-bearing `Change` per resource and composes
them through the same common `BuildPlan` used by Castle. The source ROM remains
immutable. The build report records total changed bytes and per-resource file
offset, size and changed-byte count.

Palette resources may replace a contiguous `file_offset`/`size` range with
`replicated_file_offsets`. The latter contains one non-empty offset list per
logical color. Decode requires all physical copies of an entry to agree; encode
emits a one-byte provenance-bearing span for every copy. This keeps the Qt
palette model generic while safely covering command-embedded colors such as
Pac-Man's duplicated frightened-active PPU command.

The public flow is unchanged:

```text
rltool game inspect HonkeyPong.nes
rltool game extract HonkeyPong.nes --output resources.json
rltool game validate HonkeyPong.nes resources.json
rltool game build HonkeyPong.nes resources.json --output edited.nes
rltool game launch HonkeyPong.nes edited.nes --mame MAME --output runs
```

Adapter selection is registry-driven. The CLI does not contain a HonkeyPong
branch: the profile's `nes.resource-bundle.v1` marker selects the fixed-resource
adapter, while its remaining resource codecs select their typed encoders.

## Evidence and current limit

The project-owned test image exercises a palette, a small nametable and a CHR
tile. It proves zero-edit exactness, edits all three resource types, checks
exact changed bytes, rejects missing resources and out-of-range CHR pixels, and
runs through the same public inspect/extract/validate/build functions.

The pinned real HonkeyPong fixture extracts 96 palette bytes, one nametable and
512 CHR tiles, then rebuilds to its canonical SHA-256. A holdout edit changes
one nametable byte, reports exactly that resource and completes a two-second
MAME NES run with exit code zero and a hashed screenshot. Generated documents,
ROMs, logs and screenshots remain ignored.

The fixed-resource adapter also supports structured visual and animation codecs
used by the SMB and Castle profiles:

- `nes.metatile-2x2.v1` stores named groups of fixed four-tile records and a CHR
  tile base. Record order is preserved exactly for lossless rebuilding;
- `castle.room-cell-visuals.v1` stores the 100 horizontal left/right tile pairs
  and their two-bit palette choices. The palette value is written back to
  Castle's replicated attribute bytes (`00`, `55`, `AA`, `FF`), while the room
  renderer preserves the engine's attribute-retaining cell semantics;
- `nes.ppu-palette-packets.v1` stores named fixed-capacity PPU command packets,
  including address, orientation/repeat flags, NES color values and the
  profile's Day/Night universal background colors;
- `smb.player-frames.v1` preserves the 16 action offsets, 26 fixed 2-by-4 tile
  mappings and two swim-kick replacement tiles as one transactional resource;
- `nes.ppu-screen-packets.v1` preserves named fixed-capacity PPU display lists,
  including horizontal, vertical and repeated writes, the terminator and any
  trailing bytes. It projects commands onto editable tile and attribute grids
  and rejects edits that the original packet layout cannot represent. Optional
  fixed-width `text_fields` decode and edit mapped cells in that same tile grid,
  so Screens and Text never hold competing copies of a string;

For the byte-identical SMB1 JU fixture these codecs cover 101 background
metatiles and eight area palette packets. Together with the existing 512-tile
CHR resource they feed the native procedural level preview without flattening
SMB object streams into a tilemap. An unchanged extraction still rebuilds with
zero changed bytes.

All seven accepted SMB revisions now publish their player-frame table and its
runtime player palettes. The ordinary NES revisions use `nes` master colors; the
VS revision carries `rp2c04_0004` through the generic palette document and
preview; ANN and SMB2 expose their shorter two-row player palette tables. The
player-frame editor selects this dependency instead of an area-background
palette packet.

JU, PAL, PC10, VS and the original FDS reissue also expose seven independently
addressed fixed-width UI fields through `nes.fixed-text-fields.v1`: the status
player name and headings, lives-screen world label, time-up and game-over
messages, warp-zone welcome message and Brother name. Their offsets were checked
against the public disassembly linker labels and built PRG bytes. ANN and SMB2
do not reuse this text resource because their Japanese layouts differ.

ANN and SMB2 now publish their PRG-resident title display lists as Screens
resources. Both use the shared screen editor and CHR/palette dependencies: ANN
contains 31 commands in a 351-byte span, while SMB2 contains 31 commands in a
357-byte span with its original two trailing bytes. Their public-source PRG
builds round-trip through the codec byte-for-byte. Each resource also exposes
four embedded Text fields: `1986 NINTENDO`, `HERO GAME`, `BROTHER GAME`, and
`TOP-`. The addresses are profile-specific—including the differing copyright and
top-score positions—and were verified against both public-source PRG builds.
Cartridge, VS and original FDS title screens remain gated on a locally available
CHR fixture because that engine copies the display list from CHR rather than
storing it in PRG.

The Qt 5.15 application now supplies generic CHR, palette and text editors plus
typed nametable, packet-screen, Pac-Man screen-collection and SMB procedural
level views. Embedded packet-screen text opens in the generic Text tab while
editing the same resource used by the Screens tab. HonkeyPong remains the
open-source proof of the Kit/profile/build boundary; game-specific layouts are
added through profiles and codecs rather than new CLI branches.
