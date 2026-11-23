# Changelog

## Unreleased

- Store verified repository records outside game projects, bound to canonical
  paths and the current pinned catalog. Ignore project-supplied trust markers;
  migrate older/moved projects by comparing their scripts with the pinned
  archive. Preserve explicit consent and development overrides.
- Check executable imports and bytecode, bound repository downloads, and leave
  user Git checkouts unmarked. Pin the source of the Core dependency.
- Add original synthetic SMB fixtures for public tests, keeping full ROM-based
  integration corpora local and outside Git/releases.
- Support source-built Alien Soldier resources and improve Flicky palettes,
  sound, source upgrades and playtests. Keep repository status checks passive.
- Harden resource writes, build/cache handling and movie playback. Run shared
  portable Python tests and document installation and recognized ROM hashes.
- The public binary release target is Windows Studio. Linux payloads remain
  development builds pending a separate distribution review.

## 0.5.0

Tagged baseline:
[v0.5.0](https://github.com/Retrolarium/retrolarium-kit/tree/v0.5.0).
