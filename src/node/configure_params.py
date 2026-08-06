# Shared, mesh-wide radio settings (channel, region, preset, PSK) live in
# common/radio_config.py so node and gateway can never drift apart.
from common.radio_config import (
    CHANNEL_TELEMETRY_IDX, CHANNEL_TELEMETRY_NAME, CHANNEL_TELEMETRY_PSK_B64,
    CHANNEL_MSG_IDX, CHANNEL_MSG_NAME, CHANNEL_MSG_PSK_B64,
    LORA_REGION, LORA_PRESET, REBROADCAST_MODE, SX126X_RX_BOOSTED_GAIN,
)

# ── Node-specific settings ────────────────────────────────────────────────────

# Telemetry settings
# The broadcast CADENCES (device/environment/position) are NOT here: they live
# per node in mesh_config.json under "intervals", because the gateway's PDR
# tracker measures against the very same numbers. Two copies would drift and the
# receiver would infer losses against a cadence the node was never given.
TELEMETRY_DEV_MEAS_ENABLED = True
TELEMETRY_ENV_MEAS_ENABLED = True

# Sensing node role choice
DEVICE_ROLE_CLIENT = "CLIENT"
DEVICE_ROLE_SENSOR = "SENSOR"

# Hop limit: must be (required_hops_to_gateway + 1)
REQUIRED_HOPS_TO_GATEWAY = 1            # <-- set this per node (e.g., node1=2, node2=1, node3=1)
HOP_LIMIT = REQUIRED_HOPS_TO_GATEWAY + 1

# GPS settings (optional)
# position_broadcast_secs lives in mesh_config.json ("intervals".position) —
# see the telemetry note above.
GPS_MODE = "ENABLED"
GPS_UPDATE_INTERNAL_INTERVAL = 300               # [seconds] local fix, no airtime

# Smart position broadcast defaults to TRUE in firmware and adds
# movement-triggered position packets on top of the periodic timer. That breaks
# the fixed-cadence assumption the gateway's PDR estimator relies on (the extra
# packets show up as `early_count` instead of improving the ratio), so it is
# switched off explicitly on measured nodes.
POSITION_BROADCAST_SMART_ENABLED = False
