from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(config_dir: Path = ROOT / "config") -> dict:
    settings = yaml.safe_load((config_dir / "settings.yaml").read_text(encoding="utf-8"))
    feeds = yaml.safe_load((config_dir / "feeds.yaml").read_text(encoding="utf-8"))
    settings["feeds"] = feeds["feeds"]
    for key in ("data_dir", "public_dir"):
        path = Path(settings["paths"][key])
        settings["paths"][key] = path if path.is_absolute() else ROOT / path
    return settings
