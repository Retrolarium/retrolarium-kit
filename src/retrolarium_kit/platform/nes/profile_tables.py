"""Find where a game keeps the tables and constants a kit codec reads.

    rltool profile tables [--root GAME] [--check] [--list REVISION:LABEL]

The kit reads what a game draws with (scenery tables, metatile numbers) from
the image built from the game's own disassembly, so none of it is copied
into the kit. A profile only records where each value lies. This works those
places out from the ca65 debug files of the game's builds and prints them as
the "tables" parameter of an nes.byte-tables.v1 resource, per revision; with
--check it compares them with the game's config/game_profile.json instead,
so a source change that moves a table fails the game's tests.

The spec belongs to the game: its repository keeps it as
config/profile_tables.json, beside the labels it names. It names every
value without its contents: a table by its label
(its size runs to the next label unless given), and a constant an
instruction loads by the label before it and which immediate operand it is
among the instructions assembled after that label ("immediate": 0 is the
first). Lines a revision does not assemble are skipped, so one spec serves
every revision built from the same source.

Each revision says which debug file to read and how offsets count: "image"
from the start of the output file, or "payload" from the start of the
segment the value lies in (an FDS file or a Vs. PRG payload).
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

IMMEDIATE = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*:\s*)?(?:LD[AXY]|CMP|CP[XY]|ORA|AND|ADC|SBC|EOR)\s+#", re.I)
CODEC = "nes.byte-tables.v1"


class ProfileTablesError(ValueError):
    """A spec, debug file or profile the tables cannot be worked out from."""


@dataclass(frozen=True)
class Segment:
    start: int
    size: int
    output_offset: int | None


def _fields(line: str) -> dict[str, str]:
    result = {}
    for part in line.split("\t", 1)[1].split(","):
        key, _, value = part.partition("=")
        result[key] = value.strip('"')
    return result


class DebugInfo:
    """The parts of an ld65 --dbgfile the tables need."""

    def __init__(self, path: Path, root: Path):
        if not path.is_file():
            raise ProfileTablesError(f"{path} is not here: build the revision first")
        self.files: dict[int, Path] = {}
        self.segments: dict[int, Segment] = {}
        self.spans: dict[int, tuple[int, int, int]] = {}
        self.line_spans: dict[tuple[int, int], list[int]] = {}
        self.lines: dict[int, tuple[int, int]] = {}
        self.symbols: dict[str, tuple[int, int]] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            kind = raw.split("\t", 1)[0]
            if kind not in {"file", "seg", "span", "line", "sym"}:
                continue
            item = _fields(raw)
            if kind == "file":
                # Relative source names count from the game's root, where it builds.
                self.files[int(item["id"])] = root / Path(item["name"].replace("\\", "/"))
            elif kind == "seg":
                self.segments[int(item["id"])] = Segment(
                    int(item["start"], 16), int(item["size"], 16), int(item["ooffs"]) if "ooffs" in item else None
                )
            elif kind == "span":
                self.spans[int(item["id"])] = (int(item["seg"]), int(item["start"]), int(item["size"]))
            elif kind == "line":
                key = (int(item["file"]), int(item["line"]))
                self.lines[int(item["id"])] = key
                if "span" in item:
                    self.line_spans.setdefault(key, []).extend(int(span) for span in item["span"].split("+"))
            elif kind == "sym" and item.get("type") == "lab" and "val" in item:
                definition = int(item["def"].split("+")[0])
                self.symbols[item["name"]] = (int(item["val"], 16), definition)
        self.addresses = sorted(value for value, _ in self.symbols.values())

    def label(self, name: str) -> int:
        if name not in self.symbols:
            raise ProfileTablesError(f"label {name} is not in the debug file")
        return self.symbols[name][0]

    def next_label_after(self, address: int) -> int:
        index = bisect_right(self.addresses, address)
        if index == len(self.addresses):
            raise ProfileTablesError(f"no label follows ${address:04X}")
        return self.addresses[index]

    def _assembled_after(self, name: str):
        """The source lines assembled after label name, as (span, text)."""
        if name not in self.symbols:
            raise ProfileTablesError(f"label {name} is not in the debug file")
        file_id, line = self.lines[self.symbols[name][1]]
        source = self.files[file_id].read_text(encoding="utf-8").splitlines()
        for number in range(line + 1, len(source) + 1):
            spans = self.line_spans.get((file_id, number))
            if spans:
                yield self.spans[spans[0]], source[number - 1].split(";", 1)[0], number

    def position(self, segment: int, start: int) -> tuple[Segment, int]:
        """A span start as (segment, address in the segment's own counting).
        Spans count from the segment start; label values may not (a source
        that sets its own origin), so placements go through spans."""
        record = self.segments[segment]
        return record, record.start + start

    def table(self, name: str) -> tuple[Segment, int]:
        (segment, start, _), _, _ = next(self._assembled_after(name))
        return self.position(segment, start)

    def immediates(self, name: str):
        """Each immediate instruction assembled after label name, in order,
        as (segment, operand address, source text)."""
        for (segment, start, size), text, number in self._assembled_after(name):
            if not IMMEDIATE.match(text):
                continue
            if size != 2:
                raise ProfileTablesError(f"line {number} after {name} is not a two-byte instruction")
            record, address = self.position(segment, start + 1)
            yield record, address, text.strip()

    def immediate(self, name: str, which: int) -> tuple[Segment, int]:
        for index, (segment, address, _) in enumerate(self.immediates(name)):
            if index == which:
                return segment, address
        raise ProfileTablesError(f"{name} has fewer than {which + 1} immediates after it")


def resolve(entries: list[dict], debug: DebugInfo, mode: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for entry in entries:
        if "immediate" in entry:
            (segment, address), size = debug.immediate(entry["after"], int(entry["immediate"])), 1
        else:
            segment, address = debug.table(entry["label"])
            value = debug.label(entry["label"])
            size = int(entry["size"]) if "size" in entry else debug.next_label_after(value) - value
        if mode == "image":
            if segment.output_offset is None:
                raise ProfileTablesError("an image offset needs a segment written to the output file")
            offset = segment.output_offset + address - segment.start
        elif mode == "payload":
            offset = address - segment.start
        else:
            raise ProfileTablesError(f"unknown offset mode {mode!r}")
        result[entry["name"]] = {"file_offset": offset, "size": size}
    return result


def match(settings: dict, resolved: dict, game_root: Path) -> dict[str, dict[str, int]]:
    """Offsets in a revision the disassembly does not build: each table of a
    built reference revision, found once in this revision's image."""
    reference = (game_root / settings["reference_image"]).read_bytes()
    image = (game_root / settings["image"]).read_bytes()
    result = {}
    for name, place in resolved[settings["revision"]].items():
        if place["size"] < 4:
            raise ProfileTablesError(f"{name}: a constant this short cannot be found by its bytes")
        needle = reference[place["file_offset"] : place["file_offset"] + place["size"]]
        found = image.find(needle)
        if found < 0 or image.find(needle, found + 1) >= 0:
            raise ProfileTablesError(f"{name} is not in {settings['image']} exactly once")
        result[name] = {"file_offset": found, "size": place["size"]}
    return result


def load_spec(game_root: Path, spec_path: Path | None = None) -> dict:
    path = spec_path or game_root / "config" / "profile_tables.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileTablesError(f"cannot read {path}: {exc}") from exc


def resolve_all(game_root: Path, spec: dict, revisions: set[str] | None = None) -> dict[str, dict]:
    """Every revision's tables: only the given revisions, and a revision
    found by match only when its image and its reference are here."""
    output: dict[str, dict] = {}
    for revision, settings in spec["revisions"].items():
        if "match" in settings or (revisions is not None and revision not in revisions):
            continue
        debug = DebugInfo(game_root / settings["dbg"], game_root)
        output[revision] = resolve(spec["engines"][settings["engine"]], debug, settings["offsets"])
    for revision, settings in spec["revisions"].items():
        if "match" not in settings or (revisions is not None and revision not in revisions):
            continue
        found = settings["match"]
        if (
            found["revision"] in output
            and (game_root / found["image"]).is_file()
            and (game_root / found["reference_image"]).is_file()
        ):
            output[revision] = match(found, output, game_root)
    return output


def listing(game_root: Path, spec: dict, which: str) -> list[str]:
    """The immediates a revision assembles after a label, to write a spec."""
    revision, _, label = which.partition(":")
    debug = DebugInfo(game_root / spec["revisions"][revision]["dbg"], game_root)
    return [f"{index} {text}" for index, (_, _, text) in enumerate(debug.immediates(label))]


def _profile_tables(profile: dict, revision_id: str, names: set[str]) -> dict:
    """A revision's tables as config/game_profile.json records them: the
    byte-tables resource naming these tables, from the revision's own
    resources or from the shared ones with the revision's override."""
    revision = next((r for r in profile.get("revisions", []) if r["id"] == revision_id), None)
    if revision is None:
        raise ProfileTablesError(f"{revision_id} is not a revision of the profile")
    own = revision.get("resources")
    resources = [
        r
        for r in (own if own is not None else profile.get("resources", []))
        if r.get("codec") == CODEC and names <= set(r.get("parameters", {}).get("tables", {}))
    ]
    if len(resources) != 1:
        raise ProfileTablesError(f"{revision_id}: the profile has {len(resources)} {CODEC} resources with these tables")
    resource = resources[0]
    tables = resource["parameters"]["tables"]
    if own is None:
        for override in revision.get("resource_overrides") or []:
            if override.get("id") == resource["id"] and "tables" in override.get("parameters", {}):
                tables = override["parameters"]["tables"]
    return tables


def check(game_root: Path, spec: dict, revisions: set[str] | None = None) -> list[str]:
    """Where config/game_profile.json disagrees with the tables the builds
    give (empty when it agrees)."""
    path = game_root / "config" / "game_profile.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileTablesError(f"cannot read {path}: {exc}") from exc
    built = resolve_all(game_root, spec, revisions)
    problems = []
    for revision, tables in built.items():
        settings = spec["revisions"][revision]
        engine = settings.get("engine") or spec["revisions"][settings["match"]["revision"]]["engine"]
        names = {entry["name"] for entry in spec["engines"][engine]}
        recorded = _profile_tables(profile, revision, names)
        for name, place in tables.items():
            have = recorded.get(name)
            if have != place:
                problems.append(f"{revision} {name}: the profile has {have}, the build gives {place}")
    return problems
