"""Retrolarium Kit public package."""

__version__ = "0.5.0"


def _find_core() -> None:
    # The kit builds on retrolarium_core. An installed kit gets it as a
    # dependency; a source checkout finds it beside the kit, in the monorepo
    # (retrolarium-core/python) or with the repositories cloned side by side.
    import importlib.util
    import sys
    from pathlib import Path

    if importlib.util.find_spec("retrolarium_core") is not None:
        return
    here = Path(__file__).resolve()
    for root in (here.parents[3], here.parents[3].parent):
        candidate = root / "retrolarium-core" / "python"
        if (candidate / "retrolarium_core").is_dir():
            sys.path.append(str(candidate))
            return


_find_core()
