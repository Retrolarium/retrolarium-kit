# Similar assembly routines

`rltool asm similar` finds repeated and similar instruction blocks in NES
(6502), SNES (65816) and Genesis/Mega Drive (68000) sources. It works without
building a ROM and writes no source changes.

```sh
rltool asm similar --platform nes --root path/to/doraemon_src
rltool asm similar --platform snes --root path/to/snes-game --json matches.json
rltool asm similar --platform genesis --root path/to/sonic3k_src --include "*.s" \
  --left "src/*/s3/*" --right "src/*/sk/*" --limit 100 --json matches.json
```

Source paths after the command select files or directories below `--root`. The
default is `src`. The scanner reads `.asm`, `.s`, `.inc`, `.a65` and `.a` files.
It skips build outputs, private data, virtual environments and vendor
directories. `--include` and `--exclude` are repeatable wildcards over
root-relative paths. As with Python's `fnmatch`, `*` can match path separators.

`--left` and `--right` keep pairs with one source on each side. For the Sonic
example, the patterns select the sibling engines even when their profile
directory occurs at a different depth. Already shared files are outside those
sides. Split mixed-CPU source trees with the path filters. The Genesis selector
compares 68000 instructions; it skips source regions explicitly switched to a
different CPU.

## What the scores mean

The first score compares instruction shape, including the mnemonic, its size
suffix, addressing punctuation and registers. The second compares operands as
well. Numeric notation is canonicalized, so `$0A` and decimal `10` compare
equally. Immediate values, memory addresses and external symbol names remain
distinct. Local labels compare by their target instruction position, including
AS `+`/`-` and ca65 `:+`/`:-` branches.

`same-normalized-source` means the normalized instruction sequences match.
`similar-instructions` retains operand or instruction differences for review.
These categories describe source text. A candidate still needs a review of
callers, ABI, data, placement and runtime behavior, followed by the game's build
and byte/symbol checks.

For 65816, `.a8`/`.a16` and `.i8`/`.i16` declarations and literal `REP`/`SEP`
masks contribute accumulator/index width context. Unknown masks and status
restoration make that context unknown. This follows source order, without
simulating control flow or inferring a caller's CPU mode.

## Boundaries and limits

The parser uses ca65 `.proc` blocks and `End of function` comments where
available. Other blocks are inferred from labels, direct call targets, terminal
instructions and directive barriers. Each match reports its boundary method and
source lines. Data, includes and unknown operations break a contiguous block.
Macro and repetition definitions are skipped rather than expanded.

Conditional branches of the assembly source are scanned separately and marked as
conditional. The command does not evaluate project defines, expand macros or
follow source includes as the assembler would. Assembler-specific substitutions
should be inspected through a prepared source view before treating a match as a
complete procedure.

The defaults retain blocks of 6--256 instructions, at least 80% shape similarity
and 50% operand similarity. Adjust these with `--min-instructions`,
`--max-instructions`, `--threshold` and `--operand-threshold`.

The search uses four-instruction seeds (shorter when the minimum block size is
below four), omitting seeds present in more than 64 blocks. It also checks exact
normalized sequences independently of seeds. The JSON diagnostics count omitted
common seeds and skipped short/long blocks. A candidate-pair ceiling fails
explicitly when a scan needs narrower inputs. `--max-candidates` controls that
ceiling. `--limit` controls only the reported result count; the report retains
the total number of matching pairs.

The JSON includes the selected CPU, settings, a fingerprint of all source
inputs, counts, diagnostics, scores and both source locations for every reported
pair. It contains no ROM bytes. Ranking combines matched length, shape
similarity and operand similarity, following the approach of the original
Doraemon research scanner with its game-specific bank and address assumptions
removed.
