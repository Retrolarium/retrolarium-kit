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

The JU, PAL, VS and FDS resource layouts were decoded and re-encoded
byte-identically against local open-source build artifacts. The JU and FDS
artifacts contain populated stream data and exercise the semantic timeline and
preview renderer. The generated PAL and VS source assets in that checkout are
entirely zero-filled (1601 and 1745 bytes respectively), so they verify storage
shape and exact preservation, but not real regional music content. A populated
PAL/VS image or source asset is still required for that gate. PC10 inherits the
JU NROM layout but still needs an independent PC10 artifact audit. ANN and SMB2
advertise `audio` through the separate `fds.synthesis.v1` contract; they do not
pretend that their FDS-specific ending suite uses this APU stream layout.

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
same undo history as raw-byte edits. The Studio tab advertises `edit`,
`midi-import`, `restore`, `undo` and `wav-preview`.

The Python authoring layer and Qt editor can render the currently selected
logical song to deterministic mono 16-bit PCM or WAV and mute any combination
of the four channels. Selector context chooses the event/area engine flags,
the hurry-up length-table adjustment, and the castle-clear, area, water/event,
or hardware pulse envelope. Profiles also select NTSC or PAL frame and CPU
timing. The file-oriented operation is available without the GUI:

```text
rltool audio render-smb-wav game.json --resource music --selector area_water --header water --mute noise --output water.wav
```

Guarded MIDI import accepts a monophonic, contiguous note sequence for square 2,
square 1 or triangle. It quantizes pitches against the document's APU period
table and durations against the selector's accessible eight-entry length table.
The import is rejected if timing changes, the replacement exceeds the bytes
currently consumed by that channel, or any changed byte is read by another
header, channel or length-table context. Preview performs the same checks
without writing; apply writes a separate JSON document atomically:

```text
rltool audio preview-smb-midi game.json --midi melody.mid --resource music --selector area_water --target-channel square2
rltool audio import-smb-midi game.json --midi melody.mid --output edited.json --resource music --selector area_water --target-channel square2
```

The Qt editor exposes the same preview/apply boundary. Applied bytes enter the
ordinary document undo stack as one MIDI-import operation and still require an
explicit save.

The renderer is an authoring preview with deterministic noise and a simplified
linear mix, not a cycle-accurate NES APU emulator. Final validation remains an
exact build followed by playtest in the configured emulator.

Piano-roll editing, direct in-editor playback and complete triangle/noise
register timing remain later gates. ANN/SMB2 FDS ending wave and volume
programs are covered by
[`Retrolarium_FDS_Synthesis_Authoring_Contract.md`](Retrolarium_FDS_Synthesis_Authoring_Contract.md).
