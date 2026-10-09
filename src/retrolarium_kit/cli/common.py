"""What every command group shares."""

from __future__ import annotations

import json


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))
