"""Load rules.yaml once."""
from __future__ import annotations

import functools
import os

import yaml

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "config",
    "rules.yaml",
)


@functools.lru_cache(maxsize=None)
def load_rules_config(path: str | None = None) -> dict:
    cfg_path = path or CONFIG_PATH
    with open(cfg_path) as f:
        return yaml.safe_load(f)
