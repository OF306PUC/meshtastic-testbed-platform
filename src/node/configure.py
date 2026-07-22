import sys
import json
import argparse
from pathlib import Path

# Make the `src/` package root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.meshtastic_cli import run, get_config_value  # noqa: E402
from node import configure_params as node_params  # noqa: E402

# Resolve relative to repo root (this file lives at <root>/src/node/configure.py),
# so it works regardless of the current working directory.
MESH_CONFIG_PATH = str(Path(__file__).resolve().parents[2] / "mesh_config.json")


def load_config(node_id):
    with open(MESH_CONFIG_PATH, "r") as f:
        data = json.load(f)
    
    nodes = data["nodes_cfg"]
    if node_id not in nodes: 
        raise ValueError(f"Node ID {node_id} not found in {MESH_CONFIG_PATH}")
    return nodes[node_id]

def main():
    parser = argparse.ArgumentParser(description="Configure a LoRa mesh node using meshtastic CLI")
    parser.add_argument("--node-id", type=str, required=True, help="ID of the node to configure (e.g., '1', '2', etc.)")
    parser.add_argument("--port", type=str, default=None, help="Serial port of the device (e.g., '/dev/ttyUSB0')")

    args = parser.parse_args()
    node_id = args.node_id
    port_flag = f"--port {args.port}" if args.port else ""
    node_cfg = load_config(node_id)
    hop_limit = node_cfg["hop_limit"]
    device_role = node_cfg["device_role"]

    # radio_config only enforces the telemetry PSK; the messaging channel is
    # now mesh-wide too, so fail early if its key is missing.
    if not node_params.CHANNEL_MSG_PSK_B64:
        sys.exit(
            "ERROR: LORA_MSG_CHANNEL_PSK is not set. Copy .env.example to .env "
            "and set the messaging-channel PSK (shared mesh-wide)."
        )

    print("Starting node configuration using meshtastic CLI...")

    # LoRa config: region, preset, hop limit. sx126x_rx_boosted_gain is
    # honoured only by SX126x radios; on an SX127x T-Beam it is stored but
    # ignored (harmless), so we set it mesh-wide from radio_config.
    run(
        f"meshtastic {port_flag} --set lora.region {node_params.LORA_REGION}"
        f" --set lora.modem_preset {node_params.LORA_PRESET}"
        f" --set lora.hop_limit {hop_limit}"
        f" --set lora.sx126x_rx_boosted_gain {str(node_params.SX126X_RX_BOOSTED_GAIN).lower()}"
    )

    # Device config: role then rebroadcast mode (role varies by node position),
    # chained in ONE invocation so the CLI applies both in a single config
    # write. Role is ordered first; rebroadcast is verified afterwards, so a
    # dropped write (role reboots) is still caught by the check below.
    run(
        f"meshtastic {port_flag} --set device.role {device_role}"
        f" --set device.rebroadcast_mode {node_params.REBROADCAST_MODE}"
    )
    mesh_argv = ["meshtastic"] + (["--port", args.port] if args.port else [])
    actual = get_config_value(mesh_argv, "device.rebroadcast_mode")
    if node_params.REBROADCAST_MODE.lower() not in actual.lower():
        print(f"WARNING: device.rebroadcast_mode did not persist "
              f"(expected {node_params.REBROADCAST_MODE}, read '{actual or 'unknown'}').")
    else:
        print(f"Verified device.rebroadcast_mode = {node_params.REBROADCAST_MODE}")

    # Channel config (this may trigger radio re-init)
    run(
        f'meshtastic {port_flag} --ch-set name "{node_params.CHANNEL_TELEMETRY_NAME}" '
        f'--ch-set psk {node_params.CHANNEL_TELEMETRY_PSK_B64} '
        f'--ch-index {node_params.CHANNEL_TELEMETRY_IDX}'
    )

    # Messaging channel (index 1): with REBROADCAST_MODE=LOCAL_ONLY a node only
    # relays channels it has configured, so every node must join PUC_NET for
    # phone messages (via the BLE proxies) to traverse the mesh. Re-running
    # --ch-add on an existing channel just logs an error and continues.
    run(f'meshtastic {port_flag} --ch-add "{node_params.CHANNEL_MSG_NAME}"')
    run(
        f'meshtastic {port_flag} --ch-set psk {node_params.CHANNEL_MSG_PSK_B64} '
        f'--ch-index {node_params.CHANNEL_MSG_IDX}'
    )

    # Telemetry config
    DEV_MEAS = str(node_params.TELEMETRY_DEV_MEAS_ENABLED).lower()
    ENV_MEAS = str(node_params.TELEMETRY_ENV_MEAS_ENABLED).lower()
    run(
        f"meshtastic {port_flag} --set telemetry.device_telemetry_enabled {DEV_MEAS}"
        f" --set telemetry.environment_measurement_enabled {ENV_MEAS}"
        f" --set telemetry.device_update_interval {node_params.TELEMETRY_DEV_UPDATE_INTERVAL}"
        f" --set telemetry.environment_update_interval {node_params.TELEMETRY_ENV_UPDATE_INTERVAL}"
    )

    # GPS config
    run(
        f"meshtastic {port_flag} --set position.gps_mode {node_params.GPS_MODE}"
        f" --set position.gps_update_interval {node_params.GPS_UPDATE_INTERNAL_INTERVAL}"
        f" --set position.position_broadcast_secs {node_params.GPS_UPDATE_BROADCAST_INTERVAL}"
    )

    # Reboot to apply changes
    run(f"meshtastic {port_flag} --reboot")


if __name__ == "__main__":
    main()