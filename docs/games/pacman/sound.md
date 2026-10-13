# Pac-Man NES sound authoring contract

`pacman.sound-streams.v1` is the typed, profile-driven contract used by
Retrolarium Kit and the Studio audio editor. It covers the original 16-slot
Pac-Man pulse-channel sequencer without embedding game-specific offsets in the
codec.

## Profile layout

The resource parameters define the 16-word little-endian pointer table, the
contiguous stream arena, its CPU base address and byte capacity. `storage_order`
maps logical slots to their physical order. `labels` supplies project-facing
names. `trailing_sizes` separates intentional post-stop bytes from unused `$FF`
arena padding, including after streams have been shortened and repacked.

The Pac-Man profile describes the J v1.0 and J v1.1 layout at pointer table file
offset `$309E` and arena `$30BE..$3437` (890 bytes). No ROM data is packaged
with the Kit.

## Editable document

Each logical slot retains its four-byte engine prologue and contains an ordered
command list:

- `note`: packed pitch `$00..$BF` plus a one-byte duration;
- `duration`: marker `$C0..$EF` plus a one-byte duration;
- `control`: `$F1..$F6` plus an operand, or a terminal `$F0`/`$F7..$FF` opcode.

Saving encodes all 16 streams, checks their combined capacity, packs them in the
configured physical order and rewrites every pointer transactionally. Exact
decode/build is byte-identical.

## MIDI lowering

The importer reads Standard MIDI files without an optional Python dependency. It
supports PPQ timing and tempo changes on a selected track/channel. The input
must be monophonic and contiguous because the original engine has no general
rest command. Velocity, instruments, controllers, aftertouch and pitch bends are
intentionally discarded; the conversion report lists their actual event counts
and reasons. Durations are quantized to NTSC frames and split when a single note
exceeds 255 frames. The report includes source/output time, total/max-note
quantization error, output frame and split counts, and the final arena budget.
Pitches outside the sequencer range are rejected.

The file-oriented service is also available without the GUI:

```text
rltool audio preview-midi game.json --resource sound-streams --slot 3 --midi death.mid
rltool audio import-midi game.json --resource sound-streams --slot 3 --midi death.mid --output edited.json
rltool audio render-wav edited.json --resource sound-streams --slot 3 --output death.wav
```

All commands validate the resource against its repository's (or an explicitly
selected) game profile. Preview performs the complete conversion and capacity
check without writing the document. The Qt editor displays this report and asks
for explicit confirmation before applying it. MIDI output is written atomically
only after the entire 16-slot arena passes capacity validation.

## Preview

The deterministic WAV renderer produces mono 16-bit PCM from note durations, the
NES pulse period table, duty cycle and volume. It is an authoring preview, not a
cycle-accurate APU emulator; final validation remains a build followed by
playtest in the configured emulator.
