"""Small utility to load YAML configs once and cache them."""
import functools
import os
import yaml

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


@functools.lru_cache(maxsize=None)
def load_field_mappings() -> dict:
    with open(os.path.join(CONFIG_DIR, "field_mappings.yaml")) as f:
        return yaml.safe_load(f)


@functools.lru_cache(maxsize=None)
def load_thresholds() -> dict:
    with open(os.path.join(CONFIG_DIR, "thresholds.yaml")) as f:
        return yaml.safe_load(f)
