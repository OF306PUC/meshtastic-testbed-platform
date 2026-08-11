import sys
import argparse
from pathlib import Path

# Make the `src/` package root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.meshtastic_cli import run  # noqa: E402
from gateway import configure_params as node_params  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Configure a LoRa mesh gateway receiver using meshtastic CLI")
    parser.add_argument("--port", type=str, default=None, help="Serial port of the device (e.g., '/dev/ttyUSB0')")

    args = parser.parse_args()
    port_flag = f"--port {args.port}" if args.port else ""

    # radio_config only enforces the telemetry PSK; the messaging channel is
    # now mesh-wide too, so fail early if its key is missing.
    if not node_params.CHANNEL_MSG_PSK_B64:
        sys.exit(
            "ERROR: LORA_MSG_CHANNEL_PSK is not set. Copy .env.example to .env "
            "and set the messaging-channel PSK (shared mesh-wide)."
        )

    print("Starting node configuration using meshtastic CLI...")

    # LoRa config: region, preset, and device role.
    run(
        f"meshtastic {port_flag} --set lora.region {node_params.LORA_REGION}"
        f" --set lora.modem_preset {node_params.LORA_PRESET}"
        f" --set lora.sx126x_rx_boosted_gain {str(node_params.SX126X_RX_BOOSTED_GAIN).lower()}"
        f" --set device.role {node_params.DEVICE_ROLE}"
    )

    # Telemetry channel (index 0):
    run(
        f'meshtastic {port_flag} --ch-set name "{node_params.CHANNEL_TELEMETRY_NAME}" '
        f'--ch-set psk {node_params.CHANNEL_TELEMETRY_PSK_B64} '
        f'--ch-index {node_params.CHANNEL_TELEMETRY_IDX}'
    )

    # Messaging channel (index 1): 
    run(f'meshtastic {port_flag} --ch-add "{node_params.CHANNEL_MSG_NAME}"')
    run(
        f'meshtastic {port_flag} --ch-set psk {node_params.CHANNEL_MSG_PSK_B64} '
        f'--ch-index {node_params.CHANNEL_MSG_IDX}'
    )

    # Telemetry and GPS config
    DEV_MEAS = str(node_params.TELEMETRY_DEV_MEAS_ENABLED).lower()
    ENV_MEAS = str(node_params.TELEMETRY_ENV_MEAS_ENABLED).lower()
    run(
        f"meshtastic {port_flag} --set telemetry.device_telemetry_enabled {DEV_MEAS}"
        f" --set telemetry.environment_measurement_enabled {ENV_MEAS}"
        f" --set position.gps_mode {node_params.POSITION_GPS_MODE}"
    )

    # Reboot to apply changes
    run(f"meshtastic {port_flag} --reboot")


if __name__ == "__main__":
    main()