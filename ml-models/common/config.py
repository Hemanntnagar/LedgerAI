"""Loads model_config.yaml once and caches it."""
import functools
import os
import yaml

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "model_config.yaml")


@functools.lru_cache(maxsize=None)
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)
