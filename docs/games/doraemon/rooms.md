# Graph-based World 3 generation

The Kit has one World 3 generator: a directional primitive graph with native
32px packing. It reads room materials, palettes and music context from the
user's resource document. The package contains no cartridge artwork, training
data, neural models or HTML interfaces.

## Command

Extract an unedited resource bundle from your ROM, then generate candidates:

```sh
rltool game extract original.nes --profile config/game_profile.json \
  --resource resource-bundle --output local/resources.json
rltool game doraemon-world-generate original.nes local/resources.json \
  --profile config/game_profile.json \
  --catalogue config/world3_primitives.json \
  --semantics config/world3_semantics.json \
  --constraints config/world3_generation.json \
  --room 0x0d --room 0x14 --seed 17 --count 2 --previews \
  --output local/graph-017
```

Omit `--room` to generate every non-fixed room. Repeating it limits generation
to the selected ordinary rooms; selecting a fixed event room is an error. The
input `config/world3_generation.json` supplies game-owned event rules:

```json
{
  "fixed_rooms": [],
  "extra_passable": {},
  "event_destinations": [],
  "excluded_rooms": [],
  "start": [0, 128, 128]
}
```

Populate these fields for the actual game before generating a full world.
`fixed_rooms` protects scripted layouts such as boss and bonus rooms.
`extra_passable` maps decimal room IDs to CHR tile IDs made passable by events.
`event_destinations` records conditional scripted room/x/y entries.
`excluded_rooms` identifies intentionally inaccessible storage. The optional
`start` is the starting room and player coordinates. These rules are explicit
inputs; the generator does not guess which story events to skip.

`--controls` optionally supplies a source-bound controls document from
`adjacency.default_controls`. Ports retain their original sizes and positions;
additional anchors name native block IDs at x/y positions in the 8x8 room grid.
Controls cannot overwrite a protected runtime assembly.

`--count` accepts 1..8 candidates, using consecutive seeds. Attempts are bounded
per room; an exhausted search fails without publishing a partial result. Output
includes `candidate-01.json`, and so on, and `report.json`. `--previews` adds
native-palette PNGs for generated rooms. Existing output directories are
refused. Nothing starts an emulator or changes the input resources or ROM.

## Generator and checks

Materials are conditioned on environment, palette and music class. Observed
primitive contacts and explicitly inferred, shape-compatible rock contacts
constrain native packing. The engine fixes runtime assemblies and port physics,
then constructs the remaining geometry. Original departure and arrival probes
remain clear, and negative clauses retain a blocker at every forbidden probe
set. Ordinary perimeter artwork is free to change.

Flat rock fill cannot become a new exposed coastline. Decorative assemblies need
external support, and islands are placed in a separate seeded pass with their
whole perimeter checked. Referenced original art definitions are retained; only
unused definition slots may be allocated for new packing combinations. The
hidden sixteenth primitive row contributes no neighbor constraints or player
probes.

The quality gate rejects newly sealed empty regions, new isolated player
clearance regions and an increase in disconnected broad areas measured with
32x32 pixel squares. This prevents replacing a broad source route with a new
narrow throat. Existing source restrictions remain a baseline for scripted or
narrow native passages; this is not a universal 32px minimum-width guarantee.

Both normal terrain and caller-supplied conditional terrain retain the source
room's exact passage component contracts and access to persistent objects.
Independent final audits repeat the material, support, port, quality and object
checks. Whole-world audits retain original normal routes and require every
non-excluded room to be conditionally reachable. Every candidate also passes the
normal ROM build-plan validation before the output directory is published.

## Python API

- `adjacency.generate_adjacency_world`: the same graph engine used by the CLI.
- `adjacency.audit_adjacency_world`: independently audit the generated result;
  pass the same `extra_passable` rules used for generation.
- `adjacency.default_controls`: export original ports and empty author anchors.
- `world.audit_world`: static route audit with conditional barriers and entries.
- `geometry` and `quality`: player probes, components and space-quality guards.
- `preview.room_png`: render a room from the user's CHR and palette resources.

The static model uses the game's ten player collision probes and one-pixel
horizontal/vertical reachability. Movement phases, combat, moving objects and
progression order require further simulation. Experimental samplers, annotation
UIs and machine-learning workflows are maintained outside the Kit.
