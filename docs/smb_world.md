# Retrolarium SMB World Authoring Contract

Status: implemented profile matrix; cross-revision real-image verification is
still in progress.

Codec: `smb.world.v1`

## Scope

The packaged `smb1.v1` profile recognizes seven accepted revisions from the
open `smb1_src` reconstruction. Every revision exposes typed World and Levels
resources plus shared graphics assets. Layout, payload ownership, palette model,
course banks and optional Screens/Text capabilities are revision data; the
codecs and Qt widgets do not branch on game or revision IDs. No ROM or extracted
asset is distributed in the package.

For the base JU layout, the World resource combines two independent fixed
ranges because they are one authoring workflow but are not adjacent in the
original PRG:

| Range | File offset | Size | Typed content |
| --- | ---: | ---: | --- |
| World routing | `0x1CCC` | 36 | 8 worlds and their course-to-area pointers |
| Player physics | `0x3434` | 44 | 9 named signed/unsigned tuning tables |

Both ranges are encoded into one transactional build plan. A malformed value
prevents every write; the source image is always immutable.

## Revision matrix

| Revision | Container | Course banks | Resource payloads | Extra capability |
| --- | --- | --- | --- | --- |
| JU | iNES | standard | full image | fixed UI text |
| PAL | iNES | standard | full image | fixed UI text |
| PC10 | extended iNES | standard | full image | fixed UI text |
| VS SMB | VS iNES | standard | PRG world/assets, CHR level streams/graphics | RP2C04-0004 palettes, fixed UI text |
| FDS SMB | FDS | standard | named PRG and CHR records | fixed UI text |
| ANN | FDS | normal, extended | PRG, NSMDATA2/3/4 and CHR records | title Screens with embedded Text |
| SMB2 Japan | FDS | normal, hard | PRG, SM2DATA2/3/4 and CHR records | title Screens with embedded Text |

ANN and SMB2 bind each Levels resource to its matching World resource. Their
stream spans may cross named FDS payloads but remain independently fixed-size;
profile validation proves every declared span fits its owning payload. All
other revisions retain one World/Levels pair. Profile, revision and course-bank
selectors in the Levels tab expose this context explicitly, while Day/Night is
an editor preview choice derived from the same document data.

Each `smb.levels.v1` resource also carries an immutable symbolic `rendering`
selector: `smb1` for JU/PAL/PC10/VS/FDS, `ann` for both ANN banks and `smb2` for
both SMB2 banks. The Kit derives it from profile data and rejects a changed or
unknown value on import. Retrolarium selects the engine rendering tables from
that resource field, so adding a revision does not require revision-ID branches
in the Levels widget.

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
The packaged-profile tests pin identities, resource overrides, offsets,
capacities, payload ownership and capability lists for PAL, PC10, VS, FDS, ANN
and SMB2. Public-source PRG audits additionally prove exact title-screen rebuilds
for ANN and SMB2. Full extract/edit/build/verify runs against permitted complete
source images remain the final cross-revision gate; the repository does not
embed those artifacts.
