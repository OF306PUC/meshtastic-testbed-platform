# ----------------------------------- Meshtastic gateway receiver configuration ----------------------------------- #

# Channel settings 
CHANNEL_IDX = 0
CHANNEL_NAME = "TB CPS-RTC" 
CHANNEL_PSK_B64 = "base64:9z2cyfrgTKeLdD2m0wpvJEUUh1NaHzZ05w1v1LpIEJM="

# LoRa settings: region / preset
LORA_REGION = "ANZ"
LORA_PRESET = "LONG_FAST"

# Telemetry settings
TELEMETRY_ENV_MEAS_ENABLED = False
TELEMETRY_DEV_MEAS_ENABLED = False

# Position settings (GPS) — no position broadcasting.
# The node is a static receiver, so it must never put position packets on air.
# NOTE: position.position_broadcast_secs = 0 does NOT disable broadcasting —
# the firmware falls back to its 15 min default. The only reliable way to
# silence the PositionModule is to leave the node with no position source:
# GPS disabled + no fixed position + smart broadcast off.
POSITION_GPS_MODE                = "DISABLED"
POSITION_FIXED_ENABLED           = False
POSITION_BROADCAST_SMART_ENABLED = False

# Sensing node role choice
DEVICE_ROLE = "CLIENT_MUTE"

