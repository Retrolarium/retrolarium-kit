# NES fixed-resource authoring contract

Status: the generic Kit adapter and the first open-game profile are
implemented and proven through exact rebuild, a real edit and MAME.

## Purpose

Castle Excellent exercises compressed variable-size rooms, pointer repacking
and coupled object/state tables. A second adapter is required to prove that the
public authoring operations and `GameProfile` are not Castle-specific.

`nes.resource-bundle.v1` covers independently located, fixed-capacity NES
resources. Its first profile is HonkeyPong, an MIT-licensed mapper-0 game built
from the pinned open-source fixture at commit
`f9ab7ad450ffebabec15623aa811a7564f079168`.

The profile recognizes only the 24,592-byte fixture image with SHA-256
`cce7bf2f71eb9e63de0dba15098679dde06a1da9d7553f2319aa60ea57a1193b`
and SHA-1 `efdb529788269041c3fd4adf0ce2611ca40e280b`. The ROM is a
reproducible ignored fixture; no ROM bytes are committed as part of the
profile.

## Profile evidence

The committed profile is `profiles/game_honkeypong.json`. Its ranges were
cross-checked three ways: Ophis source `.incbin` directives, the generated
`HonkeyPong_Debug.map`, and byte comparison between each upstream asset and the
built ROM.

| Resource | Codec | File range | Source symbol/asset |
|---|---|---:|---|
| logo palette | `nes.palette-table.v1` | `0x03E2+0x20` | `honkeyPal`, repeated 16-byte palette |
| title palette | `nes.palette-table.v1` | `0x0402+0x20` | `titlePal`, repeated 16-byte palette |
| game palette | `nes.palette-table.v1` | `0x0422+0x20` | `gamePal`, background plus sprite palettes |
| title screen | `nes.nametable.v1` | `0x0442+0x400` | `TitleScreen`, `title.nam` |
| character graphics | `nes.chr-2bpp.v1` | `0x4010+0x2000` | iNES CHR bank, `honkeypong.chr` |

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
logical color. Decode requires all physical copies of an entry to agree;
encode emits a one-byte provenance-bearing span for every copy. This keeps the
Qt palette model generic while safely covering command-embedded colors such as
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
exact changed bytes, rejects missing resources and out-of-range CHR pixels,
and runs through the same public inspect/extract/validate/build functions.

The pinned real HonkeyPong fixture extracts 96 palette bytes, one nametable and
512 CHR tiles, then rebuilds to its canonical SHA-256. A holdout edit changes
one nametable byte, reports exactly that resource and completes a two-second
MAME NES run with exit code zero and a hashed screenshot. Generated documents,
ROMs, logs and screenshots remain ignored.

The Qt 5.15 application now supplies generic CHR and palette editors plus
typed nametable and Pac-Man screen-collection views. HonkeyPong remains the
open-source proof of the Kit/profile/build boundary; game-specific layouts are
added through profiles and codecs rather than new CLI branches.
