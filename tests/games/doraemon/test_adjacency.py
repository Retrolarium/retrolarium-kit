from __future__ import annotations

from copy import deepcopy
import random
import unittest

from games.doraemon.fixtures import primitive_fixture as fixture, world_fixture
from retrolarium_kit.core.authoring import AuthoringError
from retrolarium_kit.games.doraemon.world3.adjacency import (
    NeighborGraph,
    PackedGrammar,
    _assemblies_supported,
    _island_library,
    _read_controls,
    _locks,
    _port_domains,
    _valid_graph,
    audit_adjacency_world,
    default_controls,
    generate_adjacency_world,
)
from retrolarium_kit.games.doraemon.world3.document import resource
from retrolarium_kit.games.doraemon.world3.geometry import SIDES, clearance, edge_positions, room_tiles
from retrolarium_kit.games.doraemon.world3.primitives import small_grid


class DirectionalGraphTests(unittest.TestCase):
    def test_semantic_closure_adds_rim_contacts_without_licensing_flat_fill(self):
        hierarchy, catalogue = fixture()
        grid = [[0] * 16 for _ in range(16)]
        grid[8][8] = 10
        raw = NeighborGraph.learn([grid], catalogue, hierarchy)
        graph = NeighborGraph.learn([grid], catalogue, hierarchy, semantic_closure=True)
        cap = catalogue["families"]["wet-rock"]["shapes"]["n"][0]
        self.assertFalse(raw.allows(0, cap, 2))
        self.assertTrue(graph.allows(0, cap, 2))
        self.assertIn((0, cap), graph.inferred[2])
        self.assertTrue(graph.safe_coastline(0, cap, 2))
        self.assertFalse(graph.safe_coastline(0, 10, 2))

    def test_bottom_packing_does_not_require_hidden_vertical_contacts(self):
        hierarchy, catalogue = fixture()
        hierarchy["big_blocks"][1] = [0, 1, 10, 10]
        grid = [[0] * 16 for _ in range(16)]
        grid[4][4] = 1
        grammar = PackedGrammar.learn(hierarchy, [grid], [[[0] * 8 for _ in range(8)]], {0, 1, 10}, catalogue)
        self.assertNotIn(1, grammar.eligible)
        self.assertIn(1, grammar.bottom_eligible)

    def test_floor_cover_cannot_count_its_own_body_as_a_foundation(self):
        grid = [[0] * 16 for _ in range(16)]
        rules = [{"kind": "background"} for _ in range(256)]
        rules[1]["kind"] = "floor-cover"
        rules[2]["kind"] = "structural-rock"
        grid[7][8] = grid[8][8] = 1
        self.assertFalse(_assemblies_supported(grid, rules, set(), free_geometry=True))
        grid[9][8] = 2
        self.assertTrue(_assemblies_supported(grid, rules, set(), free_geometry=True))

    def test_graph_counts_are_directional_and_exclude_hidden_rows(self):
        hierarchy, catalogue = fixture()
        grid = [[0] * 16 for _ in range(16)]
        grid[8][8] = 10
        grid[15][8] = 11
        graph = NeighborGraph.learn([grid], catalogue, hierarchy)
        self.assertEqual(1, graph.counts[1][0, 10])
        self.assertEqual(1, graph.counts[3][10, 0])
        self.assertFalse(any(11 in pair for counts in graph.counts for pair in counts))

    def test_native_flat_cut_is_not_permission_for_new_flat_coastlines(self):
        hierarchy, catalogue = fixture()
        grid = [[0] * 16 for _ in range(16)]
        grid[8][8] = 10
        graph = NeighborGraph.learn([grid], catalogue, hierarchy)
        self.assertTrue(graph.allows(0, 10, 1))
        self.assertFalse(graph.safe_coastline(0, 10, 1))
        self.assertTrue(_valid_graph(grid, graph, set(), grid))
        changed = deepcopy(grid)
        changed[8][10] = 10
        self.assertFalse(_valid_graph(changed, graph, set(), grid))

    def test_packing_preserves_anchors_and_does_not_invent_quadruples(self):
        hierarchy, catalogue = fixture()
        hierarchy["big_blocks"][1] = [10] * 4
        cells = [[0] * 8 for _ in range(8)]
        cells[3][3] = 1
        grammar = PackedGrammar.learn(hierarchy, [small_grid(hierarchy, cells)], [cells], {0, 10}, catalogue)
        self.assertEqual({0, 1}, grammar.eligible)
        domains = [3] * 64
        domains[27] = 2
        result = grammar.collapse(domains, set(), random.Random(4))
        self.assertIsNotNone(result)
        self.assertEqual(1, result[3][3])
        self.assertTrue({i for line in result for i in line} <= {0, 1})
        bad = [1] * 64
        bad[27] = 0
        self.assertIsNone(grammar.collapse(bad, set(), random.Random(4)))

    def test_island_library_keeps_whole_isolated_components_and_phase(self):
        hierarchy, catalogue = fixture()
        s = catalogue["families"]["wet-rock"]["shapes"]
        grid = [[0] * 16 for _ in range(16)]
        grid[6][6:8] = [s["nw"][0], s["ne"][0]]
        grid[7][6:8] = [s["sw"][1], s["se"][1]]
        ids = {v for line in grid for v in line} - {0}
        patterns = _island_library([grid], hierarchy, ids)
        self.assertEqual(1, len(patterns))
        self.assertEqual((2, 2, (0, 0)), (patterns[0]["width"], patterns[0]["height"], patterns[0]["phase"]))
        self.assertEqual(4, len(patterns[0]["cells"]))
        self.assertEqual([], _island_library([grid], hierarchy, ids - {s["nw"][0]}))

    def test_decoration_support_is_independent_of_observed_frequency(self):
        grid = [[0] * 16 for _ in range(16)]
        rules = [{"kind": "background"} for _ in range(256)]
        rules[1]["kind"] = "coral-decoration"
        rules[2]["kind"] = "structural-rock"
        grid[8][8] = 1
        self.assertFalse(_assemblies_supported(grid, rules, set()))
        grid[9][8] = 2
        self.assertTrue(_assemblies_supported(grid, rules, set()))


class AnchoredWorldTests(unittest.TestCase):
    def test_ports_mode_pins_events_instead_of_the_room_frame(self):
        hierarchy, _ = fixture()
        cells = [[0] * 8 for _ in range(8)]
        hierarchy["big_blocks"][1] = [10] * 4
        cells[3][3] = 1
        policy = {i: "terrain" for i in range(256)}
        policy[10] = "event-only"
        self.assertEqual({(3, 3)}, _locks(hierarchy, cells, policy))

    def test_port_physics_allows_new_boundary_geometry_but_rejects_extra_exits(self):
        hierarchy, _ = fixture()
        hierarchy["big_blocks"][1] = [10] * 4
        hierarchy["small_blocks"][3] = [0x40, 0x40, 0, 0]
        hierarchy["big_blocks"][2] = [10, 10, 3, 3]
        hierarchy["big_blocks"][3] = [3, 3, 0, 0]
        source = [[0] * 8 for _ in range(8)]
        source[0][3] = 1
        masks, clauses = _port_domains(hierarchy, source, [()])

        def assignment(cells):
            return [1 << i for line in cells for i in line]

        def satisfies(cells):
            domains = assignment(cells)
            return all(a & b for a, b in zip(domains, masks)) and all(
                any(domains[p] & mask for p, mask in clause) for clause in clauses
            )

        thinner = deepcopy(source)
        thinner[0][3] = 2
        self.assertTrue(satisfies(source))
        self.assertTrue(satisfies(thinner))
        original_rows = clearance(room_tiles(hierarchy, source))
        new_rows = clearance(room_tiles(hierarchy, thinner))
        for side in SIDES:
            for landing in (False, True):
                self.assertEqual(
                    edge_positions(original_rows, side, arrival=landing),
                    edge_positions(new_rows, side, arrival=landing),
                )
        extra_exit = deepcopy(source)
        extra_exit[0][3] = 3
        self.assertTrue(masks[3] & (1 << 3))
        self.assertFalse(satisfies(extra_exit))

    def test_free_world_has_no_implicit_frame_anchors(self):
        document, catalogue, semantics = world_fixture()
        original = deepcopy(document)
        result, report = generate_adjacency_world(
            document,
            catalogue=catalogue,
            semantics=semantics,
            fixed_rooms=[i for i in range(64) if i != 10],
            seed=42,
            attempts_per_room=4,
            island_probability=0,
        )
        room = report["rooms"][10]
        self.assertEqual([], room["locked_cells"])
        self.assertEqual("ports", report["boundary_mode"])
        self.assertEqual(original, document)
        self.assertEqual([], audit_adjacency_world(document, result, catalogue, semantics, report)["violations"])

    def test_controls_use_explicit_eight_pixel_tile_sizes(self):
        document, _, _ = world_fixture()
        controls = default_controls(document)
        self.assertEqual(64, len(controls["rooms"]))
        openings = controls["rooms"][10]["openings"]
        self.assertIn({"side": "north", "offset_tiles": 0, "size_tiles": 32, "tile_pixels": 8}, openings)
        self.assertIn({"side": "east", "offset_tiles": 0, "size_tiles": 30, "tile_pixels": 8}, openings)

    def test_invalid_controls_fail_instead_of_silently_changing_topology(self):
        document, _, _ = world_fixture()
        controls = default_controls(document)
        controls["rooms"][10]["openings"][0]["size_tiles"] = 31
        with self.assertRaisesRegex(AuthoringError, "opening sizes"):
            _read_controls(document, controls)
        controls = default_controls(document)
        controls["rooms"][10]["anchors"] = [{"x": 8, "y": 3, "block": 0}]
        with self.assertRaisesRegex(AuthoringError, "valid x, y"):
            _read_controls(document, controls)

    def test_world_is_repeatable_and_keeps_fixed_rooms_and_source_intact(self):
        document, catalogue, semantics = world_fixture()
        original = deepcopy(document)
        fixed = [i for i in range(64) if i != 10]
        controls = default_controls(document)
        controls["rooms"][10]["anchors"] = [{"x": 3, "y": 3, "block": 0}]
        args = dict(
            catalogue=catalogue,
            semantics=semantics,
            fixed_rooms=fixed,
            seed=17,
            attempts_per_room=4,
            island_probability=0,
            room_controls=controls,
        )
        result, report = generate_adjacency_world(document, **args)
        self.assertEqual(original, document)
        self.assertEqual((result, report), generate_adjacency_world(document, **args))
        self.assertEqual(0, report["changed_rooms"])
        self.assertEqual([], report["island_rooms"])
        self.assertEqual([], audit_adjacency_world(document, result, catalogue, semantics, report)["violations"])
        mutated = deepcopy(result)
        resource(mutated, "world3-hierarchy")["maps"][0]["rows"][11][19] = 1
        with self.assertRaisesRegex(AuthoringError, "control block"):
            audit_adjacency_world(document, mutated, catalogue, semantics, report)

    def test_source_mismatch_and_invalid_probability_fail_without_input_edits(self):
        document, catalogue, semantics = world_fixture()
        before = deepcopy(document)
        semantics["source_sha256"] = "1" * 64
        with self.assertRaisesRegex(AuthoringError, "match the source"):
            generate_adjacency_world(document, catalogue=catalogue, semantics=semantics, fixed_rooms=[])
        with self.assertRaisesRegex(AuthoringError, "probability"):
            generate_adjacency_world(
                document, catalogue=catalogue, semantics=semantics, fixed_rooms=[], island_probability=2
            )
        self.assertEqual(before, document)
