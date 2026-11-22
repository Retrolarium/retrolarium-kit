"""The kit's error family and the time a build step may take.

Every refusal the kit reports is a KitError: the CLI prints it as one line,
and a check that runs many steps (studio check) marks the step that failed
instead of stopping. The domain errors keep their older base (ValueError or
RuntimeError) beside it, so callers that catch those still work.
"""

from __future__ import annotations


class KitError(Exception):
    """Every refusal the kit reports as a one-line message."""


# How long one build step may run, in seconds: an assembler, a linker, a
# container build, a game repository's build script. A repository's split and
# check scripts have the same bound (studio/repository.py SCRIPT_TIMEOUT). The
# Studio waits 20 minutes for the kit as a backstop, so keep these below that.
BUILD_TIMEOUT = 15 * 60
