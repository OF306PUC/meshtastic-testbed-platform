# Shared, mesh-wide radio settings (channel, region, preset, PSK) live in
# common/radio_config.py so node and gateway can never drift apart.
from common.radio_config import (
    CHANNEL_IDX, CHANNEL_NAME, CHANNEL_PSK_B64, LORA_REGION, LORA_PRESET,
)

# ── Node-specific settings ────────────────────────────────────────────────────

# Rebroadcast mode: only rebroadcast packets from *your* configured channels
REBROADCAST_MODE = "LOCAL_ONLY"

# Telemetry settings
TELEMETRY_DEV_MEAS_ENABLED = True
TELEMETRY_ENV_MEAS_ENABLED = True
TELEMETRY_DEV_UPDATE_INTERVAL = 60     # [seconds]
TELEMETRY_ENV_UPDATE_INTERVAL = 60     # [seconds]

# Sensing node role choice
DEVICE_ROLE_CLIENT = "CLIENT"
DEVICE_ROLE_SENSOR = "SENSOR"

# Hop limit: must be (required_hops_to_gateway + 1)
REQUIRED_HOPS_TO_GATEWAY = 2            # <-- set this per node (e.g., node1=2, node2=1, node3=1)
HOP_LIMIT = REQUIRED_HOPS_TO_GATEWAY + 1

# GPS settings (optional)
GPS_MODE = "ENABLED"
GPS_UPDATE_INTERNAL_INTERVAL = 300               # [seconds]
GPS_UPDATE_BROADCAST_INTERVAL = 600              # [seconds] (default is 0, which means 15 min.)
