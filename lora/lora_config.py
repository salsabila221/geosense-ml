"""Single source of truth for the GeoSense LoRa radio configuration.

The file `lora/config.env` is shared by:
  - `lora_receiver.py`  (binary event pipeline, production)
  - `lora/lora_read.py` (text/CSV diagnostic reader)

Precedence: command-line argument > environment variable > config.env > default.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - dependency is in requirements
    dotenv_values = None

DEFAULTS = {
    "LORA_PORT": "",
    "LORA_BAUD": "9600",
    "LORA_NO_ACK": "0",
    "LORA_EVENT_LOG": "lora_events.jsonl",
    "LORA_TEXT_LOG": "",
    # Radio hardware settings. These are documentation/validation values;
    # E220 configuration is done in the module/firmware, not by this script.
    "LORA_CHANNEL": "18",
    "LORA_AIR_RATE": "2.4k",
    "LORA_MODE": "transparent",
    "LORA_M0": "1",
    "LORA_M1": "1",
    "LORA_RX_TX_JUMPER": "removed",
}


def config_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path:
        return Path(path).expanduser()
    env_path = os.getenv("LORA_CONFIG", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return Path(__file__).resolve().with_name("config.env")


def load_lora_config(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Return LoRa settings, merging config.env and environment variables."""
    values = dict(DEFAULTS)
    target = config_path(path)

    if dotenv_values is not None and target.is_file():
        parsed = dotenv_values(target)
        for key, value in parsed.items():
            if value is not None and key in values:
                values[key] = value.strip()

    # Environment variables win over the file (systemd/CLI can override).
    for key in DEFAULTS:
        env_value = os.getenv(key)
        if env_value is not None and env_value.strip() != "":
            values[key] = env_value.strip()

    return values


def lora_port(config: dict[str, str]) -> str:
    """Return a usable serial path, treating the installer placeholder as empty."""
    port = (config.get("LORA_PORT") or "").strip()
    if "CHANGE-ME" in port or "CHANGE_ME" in port:
        return ""
    return port


def lora_baud(config: dict[str, str]) -> int:
    try:
        return int(config.get("LORA_BAUD", "9600"))
    except (TypeError, ValueError):
        return 9600


def lora_ack_enabled(config: dict[str, str]) -> bool:
    return str(config.get("LORA_NO_ACK", "0")).strip().lower() not in {"1", "true", "yes", "on"}
