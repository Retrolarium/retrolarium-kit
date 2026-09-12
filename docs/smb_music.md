# Retrolarium SMB Music Authoring Contract

`smb.apu-music.v1` owns the fixed main APU music region used by the original
SMB1 engine. The resource keeps selector aliases, the ground-pattern schedule,
variable-size headers, one shared channel-stream arena, note periods, note
lengths and volume envelopes in a single typed document.

## Verified profile layouts

| Revision | Storage | Resource start | Selector CPU address | Size | Shared arena |
|---|---|---:|---:|---:|---:|
| JU | iNES image | `0x791D` | `0xF90D` | 1725 | 1352 |
| PAL | iNES image | `0x791E` | `0xF90E` | 1724 | 1351 |
| PC10 | iNES-PC10 image | inherited JU layout | `0xF90D` | 1725 | 1352 |
| VS SMB | `prg` payload | `0x784B` | `0xF858` | 1915 | 1495 |
| FDS SMB | `prg` payload | `0x790D` | `0xD90D` | 1725 | 1352 |

JU, PAL, VS and FDS were decoded and re-encoded byte-identically against the
local open-source build artifacts. PC10 inherits the JU NROM layout but still
needs an independent PC10 artifact audit. ANN and SMB2 do not advertise the
`audio` capability: their FDS-specific music suites require separate resource
contracts.

VS places 4-byte castle-clear and 9-byte area envelopes before its selectors;
the 40-byte water/event envelope remains trailing. Other supported revisions
store 4-, 8- and 40-byte trailing envelopes.

## Shared-stream ownership

Headers contain one absolute stream pointer and relative entry offsets for the
triangle, square 1 and optional noise channels. Multiple headers and channels
may enter at the same arena offset. `music_channel_spans()` and the equivalent
C++ document API partition the arena at every unique entry point and retain all
`header/channel` owners. Editors must update the single shared byte; they must
not create hidden per-song copies.

The channel-byte view follows the engine grammar:

- square 2: terminator, length command or note-period index;
- triangle: rest, length command or note-period index;
- square 1: alternate-control marker or packed note and length indexes;
- noise: loop marker or packed beat type and length index.

Classification always retains the original byte. It is a semantic view, not a
lossy normalization.

## Current editor boundary

The Qt editor supports selector retargeting, shared-arena bytes, note periods
and envelope values with undo, selected-value restore, atomic save and reopen.
For every arena byte it shows the physical span, all entry owners and the
channel-specific meaning. A semantic command form lowers note, length, rest,
terminator, control, beat and loop edits to canonical channel bytes and rejects
zero-byte ambiguities before they reach the shared arena. These edits use the
same undo history as raw-byte edits. The Studio tab therefore advertises only
`edit`, `restore` and `undo`.

Piano-roll editing, channel mixing, deterministic preview, guarded MIDI
lowering, and the ANN/SMB2 FDS ending wave/volume programs remain later gates.
Those capabilities must not be advertised until their typed writer and tests
exist.
