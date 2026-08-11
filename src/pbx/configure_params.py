# Shared, mesh-wide radio settings (channels, region, preset, PSKs) live in
# common/radio_config.py so node, gateway and PBX can never drift apart.
from common.radio_config import (
    CHANNEL_TELEMETRY_IDX, CHANNEL_TELEMETRY_NAME, CHANNEL_TELEMETRY_PSK_B64,
    CHANNEL_MSG_IDX, CHANNEL_MSG_NAME, CHANNEL_MSG_PSK_B64,
    LORA_REGION, LORA_PRESET, REBROADCAST_MODE, SX126X_RX_BOOSTED_GAIN,
)

# PBX-attached node settings:
# This node hangs off the nRF52840 PBX over
# UART: the PBX multiplexes up to n phones onto this single node, so the
# node's own BLE is disabled and the Stream API is exposed on UART1 instead.

# Bluetooth off: the BLE side is served by the PBX, not by this node.
BLUETOOTH_ENABLE = False

# Telemetry settings
# The broadcast cadence (device_update_interval) is NOT here: it lives per node
# in mesh_config.json under "intervals".
TELEMETRY_DEV_MEAS_ENABLED = True

# Serial module: exposes the Stream API (PhoneAPI framing) on UART1 so the
# PBX can drive this node.
SERIAL_MODULE_ENABLE = True
SERIAL_MODULE_MODE = "PROTO"
# We use U1TXD --> GPIO15 and U1RXD --> GPIO35 which are UART_DEV(1) routed pins for ESP32 chip.
# USB Console: UART_DEV(0)
# GPS:         UART_DEV(2)
SERIAL_MODULE_TXD = 15
SERIAL_MODULE_RXD = 35
SERIAL_MODULE_BAUDRATE = "BAUD_115200"
SERIAL_MODULE_TIMEOUT = 20             # [mili-seconds]

# GPS settings
GPS_MODE = "ENABLED"
GPS_UPDATE_INTERNAL_INTERVAL = 1800              # [seconds] = 30 min (local GPS fix)
POSITION_BROADCAST_SMART_ENABLED = False
