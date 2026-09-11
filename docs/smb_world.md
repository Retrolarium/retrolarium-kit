# Retrolarium SMB World Authoring Contract

Status: implemented vertical slice for the verified JU profile

Codec: `smb.world.v1`

## Scope

The packaged `smb1.v1` profile recognizes the byte-identical Japan/USA build
from the open `smb1_src` reconstruction. It exposes one typed World resource and
the complete 8 KiB CHR bank. No ROM or extracted asset is distributed in the
package.

The World resource combines two independent fixed ranges because they are one
authoring workflow but are not adjacent in the original PRG:

| Range | File offset | Size | Typed content |
| --- | ---: | ---: | --- |
| World routing | `0x1CCC` | 36 | 8 worlds and their course-to-area pointers |
| Player physics | `0x3434` | 44 | 9 named signed/unsigned tuning tables |

Both ranges are encoded into one transactional build plan. A malformed value
prevents every write; the source image is always immutable.

## Routing model

Each course route contains `area_type` (`water`, `ground`, `underground`, or
`castle`), a zero-based five-bit `area_index`, and the explicitly preserved
`alternate_bit`. The profile owns the per-world route lengths
`[5, 5, 4, 5, 4, 4, 5, 4]`; documents cannot add, remove, or reorder routes.

## Physics model

The profile owns table names, lengths, order, and signedness. Unsigned values
are constrained to `0..255`; signed speed values use `-128..127` and encode as
two's complement. This avoids treating a negative initial velocity as an opaque
hex byte while preserving exact source bytes.

## Studio integration

`rltool game extract` emits a `retrolarium.nes-resource-bundle`. `rltool studio
create` sees the resource kinds and creates `World`, `Levels`, `Graphics`, and
`Code & Analysis` tabs without a game-ID branch. The Qt World editor contains separate
World routing and Player physics pages, shared undo/redo, restore, dirty state,
and atomic save.

The verified zero-edit path is:

```text
NES disassemble -> game extract -> studio create -> studio validate
-> studio build -> studio verify -> Qt project smoke
```

For the JU open-source build the final artifact is byte-identical to the input
(`f61548fdf1670cffefcc4f0b7bdcdd9eaba0c226e3b74f8666071496988248de`).
PAL, PC10, VS, FDS, ANN, and SMB2 remain future profile additions; the codec
contains no JU-specific offsets or game-ID checks.
