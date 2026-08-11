"""Single source of truth for mesh-wide radio settings.

Node and gateway MUST agree on channel, region, preset and PSK or the mesh
won't communicate. These were previously duplicated in node/config/param_node.py
and gateway/config/param_receiver.py, which risked silent drift — defined here
once and imported by both.

The channel PSK is a shared secret: read from the environment (.env at the repo
root) and never committed. Copy .env.example to .env and set LORA_CHANNEL_PSK.
"""
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]  # <root>/src/common/radio_config.py


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency). Won't override existing env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv(_REPO_ROOT / ".env")

# Shared, non-secret radio settings (node and gateway must match)
CHANNEL_TELEMETRY_IDX  = 0
CHANNEL_TELEMETRY_NAME = "telCPS_RTC"
CHANNEL_MSG_IDX = 1
CHANNEL_MSG_NAME = "msgPUC_NET"
LORA_REGION  = "ANZ"
LORA_PRESET  = "LONG_TURBO" # Ideally: "MEDIUM_FAST"
REBROADCAST_MODE = "LOCAL_ONLY"

# SX126x RX Boosted Gain: trades a little extra power for higher RX
# sensitivity. 
SX126X_RX_BOOSTED_GAIN = True

# Shared secret: channel PSK (base64). From env; never commit the real value. 
CHANNEL_TELEMETRY_PSK_B64 = os.environ.get("LORA_TELEMETRY_CHANNEL_PSK")
CHANNEL_MSG_PSK_B64 = os.environ.get("LORA_MSG_CHANNEL_PSK")
_missing = [
    name
    for name, value in (
        ("LORA_TELEMETRY_CHANNEL_PSK", CHANNEL_TELEMETRY_PSK_B64),
        ("LORA_MSG_CHANNEL_PSK", CHANNEL_MSG_PSK_B64),
    )
    if not value
]
if _missing:
    raise RuntimeError(
        f"Channel PSK(s) not set: {', '.join(_missing)}. Copy .env.example to "
        ".env and set both base64 keys (shared by every node and the gateway)."
    )
