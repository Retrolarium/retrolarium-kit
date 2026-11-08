# Original-mechanics room route prototype

`retrolarium_kit.games.castle_excellent.reachability` is a small, bounded
mechanics oracle for planning Castle Excellent rooms. It runs the original 6502
movement, pushable-object update and player-contact procedures using the Kit's
existing CPU interpreter. It does not launch a console emulator, render the
game, or require MAME. There are no weights or ROM assets in it.

## Supported scope

- Hash-pinned, unmodified Castle Excellent Japan HSP-05 and Castlequest USA.
- Synthetic 20 by 20 rooms of 16 by 8 pixel cells, individually or in a castle.
- Walking, original jump timing, gravity, and collisions with both halves of
  each room cell.
- Up to 24 objects per room: jars, bricks, blocks, barrels, pulley controls, six
  key colors and mechanisms spawned from room cells. Pushables keep their
  original pushing and falling behavior. There is no pulling or pushing while
  airborne.
- Six door colors, key consumption, opened-door flags, collected-item effects,
  and original persistent-state/cache rules across room visits.
- Native room boundaries, entry coordinates and entrance-door handling, plus the
  pause/resume behavior on entering a room.
- Narrow/wide automatic lifts, horizontal moving platforms, pulley-operated lift
  pairs, and original stack carrying/crushing behavior.
- A rectangular goal for the player's top-left corner, in room pixels.

The input uses room-relative Y for both player and objects. The oracle adds the
game's 16-pixel HUD offset to player RAM coordinates. One tick means one
main-loop update, normally two video frames. It clears the motion-phase latch as
`FinishPlayerSprite` does after gameplay. It does not run the PPU, NMI, audio
scheduler or room-cell animation. Within this subset, those systems are not
needed to decide the tested movement routes. Audio register writes are ignored;
other hardware accesses fail explicitly.

Static cell codes whose collision handlers are harmless and whose flags are
ordinary solid/empty halves are accepted, together with the six closed door
pairs and opened door cells `$54/$55`. The oxygen-draining hazard is excluded.
Lift markers, shafts and pulley controls are also supported. Conveyors, other
animated/mutable cells and enemies are **not yet supported**. All declared rooms
are validated before search. A room with unsupported mechanics is rejected,
rather than simplified.

Player and object floor flags differ in the original game. For example, cell
`$12` supports the player but not a falling block; `$18` supports both. The
oracle reads both tables from the supplied ROM.

## Python API

```python
from pathlib import Path
from retrolarium_kit.games.castle_excellent.reachability import (
    Block, EMPTY, Oracle, RoomCase, find_route, replay,
)

cells = [[EMPTY] * 20 for _ in range(20)]
cells[18] = [0x18] * 20
cells[19] = [0x18] * 20
for row in cells:
    row[0] = row[-1] = 0x18
case = RoomCase(tuple(map(tuple, cells)), (64, 128), (96, 128, 96, 128),
                (Block(80, 128),))
oracle = Oracle(Path("original.nes").read_bytes())
result = find_route(oracle, case, max_nodes=30, max_ticks=70)
if result.status == "reachable":
    assert replay(oracle, case, result.buttons) == result.positions
```

`RoomCase.from_dict()` accepts JSON with `cells`, `start`, `goal` and optional
`blocks` (`x`, `y`, optional runtime `kind`, default `$8C`; `$8E` is a pulley
control), `keys` (same fields, default kind `$91`), `inventory` and `number`
(default 12). Keys are eight pixels wide; their X is the actual runtime
coordinate, including any desired four-pixel centering offset. Initial solid
bodies must fit without overlapping each other or solid geometry. Keys may
overlap the player, but may not be inside a solid cell.

Search and replay use the small generic `RouteOracle` protocol (`initial`,
`tick`), shared by `Oracle`, `CastleOracle` and synthetic test implementations.
The case type stays paired with its oracle; single-room and castle goals have
different coordinate shapes.

## Platforms and lifts

Mechanisms are placed as cells, just as in the original loader. The oracle runs
`ScanRoomCellObjectMarkers` / `SpawnRoomCellObject` from the supplied ROM, after
the ordinary objects. Markers become empty cells and consume runtime slots.
Their collision dimensions are read from the ROM, including 32-pixel-wide and
eight-pixel-high bodies. The frame counter starts at zero and advances only on
gameplay updates; an entry pause freezes the mechanisms too.

| Cells        | Meaning                                    | Spawned runtime type                      |
| ------------ | ------------------------------------------ | ----------------------------------------- |
| `$4B`        | Narrow automatic lift                      | `$A1`, 16 by 8 pixels                     |
| `$4C`        | Narrow lift shaft                          | None                                      |
| `$4D/$4E`    | Wide lift surface pair                     | `$A2`, 32 by 8 pixels                     |
| `$4F/$50`    | Wide lift shaft pair                       | None                                      |
| `$51`        | Horizontal moving platform                 | `$A3`, 16 by 8 pixels, initially leftward |
| `$49`, `$4A` | Pulley-operated upper/lower members        | `$A4`, `$A5`, each 16 by 8 pixels         |
| `$52`        | Pulley lift track                          | None                                      |
| `$41-$43`    | Pulley control cells above a control block | None                                      |

Wide surfaces/shafts require matching left/right halves. The adapter accepts one
complete pulley-operated pair per room, matching the original procedures'
selection of the first upper and lower member. A `$8E` control requires that
pair. The total pool limit includes static objects, keys and all markers.

Automatic lifts update when `RoomFrameCounter & $1F == $1F`. They move one
eight-pixel row, reverse at the end of their shaft or travel, and update shaft
cells. Rising movement computes the original transitive occupant closure: a
block can carry another block or key, and a supported player can ride the whole
stack. Width, contact geometry and runtime slot order matter. Disconnected
objects are not carried. A rising block blocked by the ceiling becomes the
original defeat/crushed effect (`$A8`).

Descending lifts move first; their loads follow under the original gravity and
update order, rather than being teleported downward as one rigid group. Pulley
members move two pixels when their control is pushed, subject to track and
clearance tests, with the original closure on ascent. Airborne pushing remains
disabled. Returning to a room respawns mechanisms at their initial positions and
direction, while ordinary collected/opened flags still apply.

The horizontal `$A3` object moves two pixels, reverses at an obstruction and
stops if both sides are blocked. It does **not** automatically drag standing
loads sideways. Direct overlap with a mechanism can kill the player; ordinary
solid collision prevents walking through its side. These behaviors run in the
original collision handlers, not in a new generic platform rule.

An impossible shaft can trap the original code in a same-tick reversal loop. The
instruction budget reports that as an oracle error instead of hanging. Such a
candidate must be rejected or repaired, not marked reachable. This layer still
proves only the requested route, not future stability after reaching its goal.

## Keys and doors

Inventory uses the game's RAM order: red, dark blue, yellow, green, pink, blue.
Each count is an unsigned byte; collection and consumption run in the original
code, including its byte arithmetic.

| Color     | Runtime key type | Closed door top/bottom | Inventory index |
| --------- | ---------------- | ---------------------- | --------------- |
| Red       | `$91`            | `$25/$26`              | 0               |
| Dark blue | `$90`            | `$29/$2A`              | 1               |
| Yellow    | `$95`            | `$22/$23`              | 2               |
| Green     | `$93`            | `$1C/$1D`              | 3               |
| Pink      | `$92`            | `$19/$1A`              | 4               |
| Blue      | `$94`            | `$1F/$20`              | 5               |

Door cells must form a vertical pair. A normal door opening consumes one key and
replaces the pair with `$54/$55`. The player must be grounded and satisfy
`screen_y & $0F == $08` (equivalently `room_y % 16 == 8`). Thus a corridor with
floor row 19 and player Y=136 is aligned, while floor row 18 and Y=128 is not.
The generator must respect this placement rule.

An entrance door at a horizontal room boundary is opened automatically by the
original transition code, without spending a key. This exception matters when
designing puzzles: such an entrance must not be counted as a key requirement.

## Several rooms

`castle_routes.CastleCase` contains `rooms`, `start`, `goal` and optional
initial `inventory`. Each `RoomLayout` has `number`, `cells`, optional `blocks`
and `keys`. Start is `[room_number, x, y]`; goal is
`[room_number, left, top, right, bottom]`. Use `CastleOracle(image, case)` with
the same `find_route` and `replay` functions. The console recognizes this JSON
shape automatically.

Room numbers are the original 1-100 grid, in ten columns: horizontal neighbors
are +/-1 within a row and vertical neighbors are +/-10. The adapter runs the
original transitions, room-state loading, two-room cache, persistence, and
entrance opening procedures. Only the room decoder, object loader and their
allocation tables use caller-supplied synthetic data; a small in-memory shim
connects the original procedures. PPU/audio presentation is skipped. The ROM
file is never patched or written.

The generated state tables must fit the original 141-byte object and 108-byte
door arenas, with at most 39 door anchors in a room. Missing destination rooms
or entries blocked by solid geometry/objects terminate a branch. Outer castle
edges retain the game's boundary behavior. Returning to a room reloads its
original object placements and applies the original collected/opened flags;
pushable coordinates are not permanently saved by this layer.

An entry pauses gameplay as in the original game. The route must provide a new A
press to resume; holding A across another immediate transition is not enough.
The native vertical coordinate/alignment rules are covered too. Falling can
reach a lower room; reaching an upper boundary still needs a mechanic capable of
getting the player there. A timed-lift fixture now reaches an upper neighbor
with 257 wait updates and verifies the original entrance position. Single-room
`Oracle` still terminates a branch at any attempted room transition.

## Console

```sh
python -m retrolarium_kit.games.castle_excellent.reachability \
  --rom original.nes --case room.json --output route.json \
  --max-nodes 300 --max-ticks 180 --action-ticks 4

python -m retrolarium_kit.games.castle_excellent.reachability \
  --rom original.nes --case room.json --replay route.json --output replay.json
```

A v3 report includes the synthetic case, ROM hash, search limits, button masks
for each tick, player positions, room numbers, inventory history, object records
and final RAM SHA-256. It contains no ROM bytes or machine-specific input paths.
Replay checks all of those state observations, including the final snapshot hash
(which also covers door flags and cache/persistent state). Each active object
record is `[slot, type, x, y, state, auxiliary_byte]`, recorded for every tick;
replay compares those too. Prototype v1/v2 reports must be regenerated. Exit
codes are 0 for a verified route, 1 for an unknown result, and 2 for invalid
inputs or oracle failures.

All three console search limits must be positive, including in replay mode.
Reports use UTF-8 with LF newlines on every platform. The output path must not
replace the input ROM, case or replay report.

The best-first search holds each action for a configurable number of ticks and
checks the goal after each tick. Node, tick and per-tick instruction budgets
bound the work. Full RAM snapshots are kept; distinct states are not merged by
player position alone. A found route is replayed before acceptance. The
algorithm does not claim the shortest route. **Unknown never means
unreachable**: action grouping, depth and node limits can all miss solutions. It
proves a route to one goal inside this subset, not all objectives of a complete
castle. The simple distance heuristic can need a larger budget for key detours
and backtracking; it is not a complete inventory-puzzle planner. A route need
not end on solid ground unless the goal enforces that.

## Tests and next steps

The default unit tests use original synthetic programs and graph fixtures.
Optional integration tests read caller-supplied original ROMs: set
`RETROLARIUM_CASTLE_PHYSICS_ROMS` to paths separated by the operating system's
path separator, then run
`python -m unittest discover -s tests/games/castle_excellent -t tests` from the
Kit checkout. Discovery adds the shared test helpers to the import path. Testing
both revisions also compares route inputs, positions, room numbers and
inventory. Cache-eviction fixtures leave a room through several neighbors and
return to check that keys do not respawn, doors stay open, and mechanisms reset.
Mechanism fixtures cover timing, shaft reversal, forked stacks, downward
gravity, crushing, side-contact death, platform reversal/stopping, pulley
controls, the 24-slot boundary, and native upper-room entry. These tests do not
require bundled commercial data or new dependencies.

Before learning or generating complete puzzle rooms, extend the state and oracle
coverage in this order: enemies/hazards; richer inventory goals and castle-wide
puzzle planning. Each addition needs mechanism-specific fixtures and replayable
solutions under original rules. A future generator can propose geometry and
object placements, while this layer filters candidates and preserves a witness
route for human review.
