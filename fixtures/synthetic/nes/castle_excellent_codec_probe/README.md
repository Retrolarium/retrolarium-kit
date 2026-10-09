# Castle Excellent codec probe

This directory contains an original 0BSD test oracle for the data-driven
`castle-excellent.prefix-rooms.v1` and `castle-excellent.object-stream.v1`
codecs. The ROM image is constructed in memory by the Retrolarium Kit tests and
is never committed.

The probe deliberately keeps the real format relationships while reducing the
geometry to four 2x2 rooms:

- an ASCII `0`/`1` prefix codebook in fixed PRG;
- little-endian CHR-local room pointers;
- two independently capacity-limited CHR bank arenas;
- terminated `(type, x, y)` object records;
- object-count, object-state and mutable-cell-state side tables.

`oracle.json` is independent semantic ground truth. The generated image is
mapper-3 iNES with 32 KiB PRG and four 8 KiB CHR banks.
