# Retrolarium NES text authoring contract

Status: implemented for fixed text resources and embedded screen text.

## Codec boundary

`nes.fixed-text-fields.v1` represents a named set of fixed-width strings whose
bytes may live in unrelated locations in an NES image. It is selected through a
resource codec ID; neither the public game commands nor the Qt editor branch on
a game ID.

The game profile is the immutable source of layout and encoding truth. Its
resource parameters declare:

- a non-empty, one-to-one `characters` and byte `codes` mapping;
- `none`, `upper` or `lower` case folding and a mapped padding byte;
- ordered fields with a stable key, display label, file offset and fixed width.

Field keys and byte ranges must be unique, ranges must not overlap, and every
source byte must decode through the declared map. The extracted document copies
the encoding contract and field key/label/width metadata so both Kit and GUI can
detect incompatible or tampered documents.

`nes.ppu-screen-packets.v1` may carry the same encoding contract plus ordered
`text_fields`. Each field identifies a screen key, a screen-relative tile offset
and a width that must fit one visible row. These fields are views over the
screen's existing `tiles`; the resource never stores a second string value.
Field tiles must be mapped glyphs, fields cannot overlap, and edits must remain
representable by the original PPU packet layout.

## Transactional writer

Each authored string is folded according to the profile, checked against the
glyph map, encoded and padded to its exact original width. Overlong or
unsupported input is rejected. The bundle writer turns all fields into one
multi-range `BuildPlan`; it checks the immutable source bytes at every range
before applying any replacement. Build reports retain `file_offset` for legacy
single-range resources and expose a `ranges` array for disjoint resources.

## Qt consumer

`text.fixed` plus either text-capable codec selects `NesTextEditorWidget`. The
widget lists profile labels, displays stable keys and capacities, normalizes
input before it enters the document, and supports undo/redo, current-message
restore and atomic save. Multiple typed editors referencing the same resource
document continue to share one in-memory model through `StudioEditorFactory`;
therefore a Text edit of an embedded field updates the corresponding Screens tab
without a competing document copy.

## Verification

Synthetic Kit tests prove exact zero-edit output, two-range reports, lowercase
normalization, byte-local edits, overlap rejection, metadata immutability and
unsupported-character rejection. Native tests cover document parsing,
normalization, widget undo/restore/save and factory selection. The ignored
Pac-Man Japan v1.0 fixture proves extraction of 14 messages, exact Studio build,
verification and GUI smoke; a known edit changes only offset `$355`. Japan v1.1
also passes an exact ten-resource in-memory rebuild. ANN and Japanese SMB2
public-source PRG audits prove zero-diff rebuilds of their 351-byte and 357-byte
title display lists and decode four embedded fields each; the synthetic packet
test proves that changing those tile-backed glyphs rebuilds the command bytes.
