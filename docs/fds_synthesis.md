# Retrolarium FDS Synthesis Authoring Contract

`fds.synthesis.v1` exposes the fixed FDS wave-RAM and volume-program data used
by the ANN and SMB2 Japan ending audio banks. It is deliberately separate from
`smb.apu-music.v1`: this slice owns synthesis parameters, not the song stream,
note table or FDS modulation program.

## Verified profile layout

Both revisions load their ending-audio payload at CPU address `$C5D0`. The
typed resource starts at payload offset `$0C00` / CPU address `$D1D0` and keeps
the complete `$00AF`-byte range through `$D27E` as an immutable-shape arena.

| Revision | Payload | Payload size | Resource offset | Resource size |
|---|---|---:|---:|---:|
| All Night Nippon SMB | `nsmdata3` | 3346 | `$0C00` | `$00AF` |
| SMB2 Japan / Lost Levels | `sm2data3` | 3279 | `$0C00` | `$00AF` |

The common semantic spans are:

| Control | Arena offset | Encoded size |
|---|---:|---:|
| Wave B | `$00` | 32 bytes |
| Volume program B | `$20` | 2 two-byte steps |
| Volume program A | `$24` | 6 two-byte steps |
| Wave A | `$8F` | 32 bytes |

The addresses and bytes were checked against the ANN linker map and a native
project assembler output, and against the corresponding SMB2 labels and
`SM2DATA3` payload. The generic FDS-record pipeline also proves a zero-edit
whole-image round trip and an edit crossing two physical FDS records.

## Typed encoding

Each waveform contains exactly 32 unsigned 6-bit samples (`0..63`). A volume
step is a register byte followed by a non-zero frame count (`1..255`). The
register low six bits hold the value; bit 7 selects `direct`, bit 6 selects
`increase`, and neither bit selects `decrease`.

Documents retain every byte in `arena`. Encoding copies that arena first and
then replaces only the four declared semantic spans. Names, entry counts,
sample ranges, step order, modes, frame ranges, bounds and overlap are checked
against the selected profile. Unknown bytes between and after the owned spans
are never normalized.

## Studio boundary

ANN and SMB2 Japan now advertise the `audio` capability through an
`audio.synthesis` tab. The Qt editor supports named waveform samples and named
volume-program steps, with a shared undo stack, selected-value restore and
atomic save/reopen. The tab advertises only `edit`, `restore` and `undo`.

Song sequencing, note tables, FDS modulation/sweep editing, audio rendering,
MIDI import and playback are outside this codec. They must use a later typed
resource and must not be inferred from the preserved arena bytes.
