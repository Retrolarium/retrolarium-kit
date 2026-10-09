from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from retrolarium_kit.core.authoring import (
    Address,
    AddressRange,
    AuthoringError,
    BinaryReader,
    BuildPlan,
    Change,
    ChangeSet,
    Provenance,
    Registry,
)


class AuthoringContractTests(unittest.TestCase):
    def test_typed_addresses_do_not_mix_banks_or_spaces(self) -> None:
        span = AddressRange(Address("chr", 0x100, bank=2), 0x20)
        self.assertTrue(span.contains(Address("chr", 0x11F, bank=2)))
        self.assertFalse(span.contains(Address("chr", 0x100, bank=3)))
        self.assertFalse(span.contains(Address("file", 0x100)))

    def test_binary_reader_rejects_out_of_bounds_reads(self) -> None:
        reader = BinaryReader(b"RL", "probe")
        self.assertEqual(reader.u16le(0), 0x4C52)
        with self.assertRaisesRegex(AuthoringError, "exceeds"):
            reader.read(1, 2)

    def test_build_plan_is_transactional_and_checks_expected_bytes(self) -> None:
        source = b"RETROLARIUM"
        digest = hashlib.sha256(source).hexdigest()
        provenance = Provenance(digest, "rooms", "encode")
        change = Change(
            AddressRange(Address("file", 5), 3),
            b"LAR",
            b"KIT",
            provenance,
        )
        plan = BuildPlan(digest, ChangeSet((change,)))
        self.assertEqual(plan.apply(source), b"RETROKITIUM")
        stale = Change(
            AddressRange(Address("file", 5), 3),
            b"BAD",
            b"KIT",
            provenance,
        )
        with self.assertRaisesRegex(AuthoringError, "stale change"):
            BuildPlan(digest, ChangeSet((stale,))).apply(source)
        self.assertEqual(source, b"RETROLARIUM")

    def test_build_plan_rejects_conflicting_overlaps(self) -> None:
        source = b"1234"
        digest = hashlib.sha256(source).hexdigest()
        provenance = Provenance(digest, "rooms", "encode")
        first = Change(
            AddressRange(Address("file", 1), 2), b"23", b"AB", provenance
        )
        second = Change(
            AddressRange(Address("file", 2), 1), b"3", b"C", provenance
        )
        with self.assertRaisesRegex(AuthoringError, "conflicting"):
            BuildPlan(digest, ChangeSet((first, second))).apply(source)

    def test_registry_rejects_duplicates_and_unknown_ids(self) -> None:
        registry: Registry[int] = Registry("codec")
        registry.register("probe.v1", 1)
        self.assertEqual(registry.get("probe.v1"), 1)
        with self.assertRaisesRegex(AuthoringError, "duplicate"):
            registry.register("probe.v1", 2)
        with self.assertRaisesRegex(AuthoringError, "unknown"):
            registry.get("missing.v1")


if __name__ == "__main__":
    unittest.main()
