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

# ── Shared, non-secret radio settings (node and gateway must match) ───────────
CHANNEL_IDX  = 0
CHANNEL_NAME = "TB CPS-RTC"
LORA_REGION  = "ANZ"
LORA_PRESET  = "LONG_FAST"

# ── Shared secret: channel PSK (base64). From env; never commit the real value. ──
CHANNEL_PSK_B64 = os.environ.get("LORA_CHANNEL_PSK")
if not CHANNEL_PSK_B64:
    raise RuntimeError(
        "LORA_CHANNEL_PSK is not set. Copy .env.example to .env and set the "
        "channel PSK (the base64 key shared by every node and the gateway)."
    )
