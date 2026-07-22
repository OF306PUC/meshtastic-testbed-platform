# Shared, mesh-wide radio settings (channels, region, preset, PSKs) live in
# common/radio_config.py so node, gateway and proxy can never drift apart.
from common.radio_config import (
    CHANNEL_TELEMETRY_IDX, CHANNEL_TELEMETRY_NAME, CHANNEL_TELEMETRY_PSK_B64,
    CHANNEL_MSG_IDX, CHANNEL_MSG_NAME, CHANNEL_MSG_PSK_B64,
    LORA_REGION, LORA_PRESET, REBROADCAST_MODE, SX126X_RX_BOOSTED_GAIN,
)

# ── Proxy-attached node settings ──────────────────────────────────────────────
# This node hangs off the nRF52840 BLE proxy (../meshtastic-ble-proxy) over
# UART: the proxy multiplexes up to n phones onto this single node, so the
# node's own BLE is disabled and the Stream API is exposed on UART1 instead.

# Bluetooth off: the BLE side is served by the proxy, not by this node.
BLUETOOTH_ENABLE = False

# Telemetry settings
# device_update_interval: 0 = firmware default (frequent). Set explicitly to
# 15 min so device metrics don't flood the client/mesh. 900 s = 15 min.
TELEMETRY_DEV_MEAS_ENABLED = True
TELEMETRY_DEV_UPDATE_INTERVAL = 900    # [seconds] = 15 min

# Serial module: exposes the Stream API (PhoneAPI framing) on UART1 so the
# proxy can drive this node.
SERIAL_MODULE_ENABLE = True
SERIAL_MODULE_MODE = "PROTO"
# We use U1TXD --> GPIO15 and U1RXD --> GPIO35 which are UART_DEV(1) routed pins for ESP32 chip.
# USB Console: UART_DEV(0)
# GPS:         UART_DEV(2)
SERIAL_MODULE_TXD = 15
SERIAL_MODULE_RXD = 35
SERIAL_MODULE_BAUDRATE = "BAUD_115200"
SERIAL_MODULE_TIMEOUT = 20             # [mili-seconds]

# Device role and hop limit are per-proxy (p1 vs p2 differ) and live in
# mesh_config.json — configure.py reads them via --node-id.

# GPS settings (optional)
# 0 = firmware default (broadcast default is ~15 min). Set explicitly:
#   gps_update_interval    (local fix cadence, no mesh airtime)
#   position_broadcast_secs(over-air position broadcast) -> 30 min
GPS_MODE = "ENABLED"
GPS_UPDATE_INTERNAL_INTERVAL = 1800              # [seconds] = 30 min (local GPS fix)
GPS_UPDATE_BROADCAST_INTERVAL = 1800             # [seconds] = 30 min (mesh broadcast)
