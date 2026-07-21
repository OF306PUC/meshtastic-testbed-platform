# ----------------------------------- Meshtastic gateway receiver configuration ----------------------------------- #

# Shared, mesh-wide radio settings (channel, region, preset, PSK) live in
# common/radio_config.py so node and gateway can never drift apart.
from common.radio_config import (
    CHANNEL_TELEMETRY_IDX, CHANNEL_TELEMETRY_NAME, CHANNEL_TELEMETRY_PSK_B64,
    CHANNEL_MSG_IDX, CHANNEL_MSG_NAME, CHANNEL_MSG_PSK_B64,
    LORA_REGION, LORA_PRESET,
)

# ── Gateway-specific settings (receive-only data sink) ────────────────────────

# Telemetry disabled — the gateway only sinks data, it does not sense.
TELEMETRY_ENV_MEAS_ENABLED = False
TELEMETRY_DEV_MEAS_ENABLED = False

# Position (GPS) disabled on the gateway.
POSITION_GPS_MODE = "DISABLED"

# CLIENT_MUTE: receives mesh traffic but never rebroadcasts.
DEVICE_ROLE = "CLIENT_MUTE"
