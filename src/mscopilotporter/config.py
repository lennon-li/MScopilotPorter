"""Load the user's config file (YAML). Missing file yields an empty config."""
import os

import yaml

from mscopilotporter import paths


def load_config() -> dict:
    path = paths.config_file()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a mapping")
    return data
