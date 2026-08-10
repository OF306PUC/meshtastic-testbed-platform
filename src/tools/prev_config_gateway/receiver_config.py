import subprocess
import time
import param_receiver as node_params

MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds to wait before retrying

# Keywords that indicate a transient serial error worth retrying
RETRYABLE_ERRORS = [
    "couldn't be opened",
    "Input/output error",
    "OS Error",
    "serial device",
    "write failed",
]

def is_retryable(stderr: str, stdout: str) -> bool:
    combined = (stderr + stdout).lower()
    return any(keyword.lower() in combined for keyword in RETRYABLE_ERRORS)

def run(cmd, retries=MAX_RETRIES):
    print(f"\nRunning: {cmd}")
    for attempt in range(1, retries + 1):
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode == 0 and not is_retryable(result.stderr, result.stdout):
            time.sleep(2)
            return  # success
        # Something went wrong
        print(f"ERROR (attempt {attempt}/{retries}):", result.stderr or result.stdout)
        if attempt < retries:
            print(f"Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
        else:
            print(f"Command failed after {retries} attempts. Continuing...")
            time.sleep(2)

def main():
    print("Starting node configuration using meshtastic CLI...")

    # LoRa config: region, preset, and device role
    run(
        f"meshtastic --set lora.region {node_params.LORA_REGION}"
        f" --set lora.modem_preset {node_params.LORA_PRESET}"
        f" --set device.role {node_params.DEVICE_ROLE}"
    )

    # Channel config (this may trigger radio re-init)
    run(
        f'meshtastic --ch-set name "{node_params.CHANNEL_NAME}" '
        f'--ch-set psk {node_params.CHANNEL_PSK_B64} '
        f'--ch-index {node_params.CHANNEL_IDX}'
    )

    # Telemetry and GPS config.
    # No position broadcasting: --remove-position clears any fixed position left
    # over from an earlier config, and with gps_mode DISABLED the PositionModule
    # has nothing to send. Smart broadcast is turned off as well so a position
    # arriving from a phone/PBX can't retrigger transmissions.
    DEV_MEAS    = str(node_params.TELEMETRY_DEV_MEAS_ENABLED).lower()
    ENV_MEAS    = str(node_params.TELEMETRY_ENV_MEAS_ENABLED).lower()
    FIXED_POS   = str(node_params.POSITION_FIXED_ENABLED).lower()
    SMART_BCAST = str(node_params.POSITION_BROADCAST_SMART_ENABLED).lower()
    run(
        f"meshtastic --set telemetry.device_telemetry_enabled {DEV_MEAS}"
        f" --set telemetry.environment_measurement_enabled {ENV_MEAS}"
        f" --remove-position"
        f" --set position.gps_mode {node_params.POSITION_GPS_MODE}"
        f" --set position.fixed_position {FIXED_POS}"
        f" --set position.position_broadcast_smart_enabled {SMART_BCAST}"
    )

    # Reboot to apply changes
    run("meshtastic --reboot")


if __name__ == "__main__":
    main()