"""Original test data shared by Python renderer tests and the Studio fixture exporter."""

import importlib.util
from pathlib import Path

from retrolarium_kit.games.smb.level_render import SmbLevelRendering

_spec = importlib.util.spec_from_file_location(
    "smb_test_corpus", Path(__file__).resolve().parents[3] / "tools/smb_test_corpus.py"
)
corpus_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(corpus_tool)


def synthetic_rendering() -> SmbLevelRendering:
    return SmbLevelRendering.from_resource(corpus_tool.level_tables(), "smb1")
