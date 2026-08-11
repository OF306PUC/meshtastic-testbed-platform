import sys
import argparse
from pathlib import Path

# Make the `src/` package root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import mesh_config  # noqa: E402
from common.meshtastic_cli import run, get_config_value  # noqa: E402
from node import configure_params as node_params  # noqa: E402

# Resolve relative to repo root (this file lives at <root>/src/node/configure.py),
# so it works regardless of the current working directory.
MESH_CONFIG_PATH = str(Path(__file__).resolve().parents[2] / "mesh_config.json")


def load_config(node_id):
    """Returns (node_cfg, intervals) for one node from mesh_config.json."""
    data = mesh_config.load(MESH_CONFIG_PATH)
    cfg  = mesh_config.node_cfg(data, node_id)
    return cfg, mesh_config.intervals_for(cfg)

def main():
    parser = argparse.ArgumentParser(description="Configure a LoRa mesh node using meshtastic CLI")
    parser.add_argument("--node-id", type=str, required=True, help="ID of the node to configure (e.g., '1', '2', etc.)")
    parser.add_argument("--port", type=str, default=None, help="Serial port of the device (e.g., '/dev/ttyUSB0')")

    args = parser.parse_args()
    node_id = args.node_id
    port_flag = f"--port {args.port}" if args.port else ""
    node_cfg, intervals = load_config(node_id)
    hop_limit = node_cfg["hop_limit"]
    device_role = node_cfg["device_role"]

    # Broadcast cadences come from mesh_config.json so the gateway's PDR
    # estimator measures against exactly what gets written to the radio. A kind
    # omitted there is left at whatever the firmware already holds, and the
    # gateway tracks no PDR for it.
    print(f"Broadcast cadences from mesh_config.json: {intervals}")

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
    telemetry_flags = (
        f" --set telemetry.device_telemetry_enabled {DEV_MEAS}"
        f" --set telemetry.environment_measurement_enabled {ENV_MEAS}"
    )
    if "device" in intervals:
        telemetry_flags += f" --set telemetry.device_update_interval {intervals['device']}"
    if "environment" in intervals:
        telemetry_flags += f" --set telemetry.environment_update_interval {intervals['environment']}"
    run(f"meshtastic {port_flag}{telemetry_flags}")

    # GPS config. position_broadcast_smart_enabled is set explicitly (firmware
    # default is true): with smart broadcast on, a moving node emits extra
    # position packets outside the periodic timer, which invalidates the
    # fixed-cadence assumption the gateway's PDR estimator depends on.
    SMART_POS = str(node_params.POSITION_BROADCAST_SMART_ENABLED).lower()
    gps_flags = (
        f" --set position.gps_mode {node_params.GPS_MODE}"
        f" --set position.gps_update_interval {node_params.GPS_UPDATE_INTERNAL_INTERVAL}"
        f" --set position.position_broadcast_smart_enabled {SMART_POS}"
    )
    if "position" in intervals:
        gps_flags += f" --set position.position_broadcast_secs {intervals['position']}"

    # A surveyed position, when mesh_config.json declares one, is provisioned as
    # the node's fixed position. For a stationary node this beats a GPS fix on
    # three counts: it does not drift between readings, it does not depend on sky
    # view from under the canopy, and it is accurate enough to compute inter-node
    # distances from — which is what the adjacency matrix needs. The node keeps
    # broadcasting it on the position cadence, so the PDR flow is unaffected.
    #
    # NOTE: a reduced `module_settings.position_precision` on the CHANNEL
    # quantises lat/lon on transmission, so a fixed position gets rounded onto a
    # grid just as a GPS fix does. Check that first, or exact coordinates in and
    # still-identical coordinates out.
    fixed = mesh_config.position_for(node_cfg)
    if fixed is not None:
        gps_flags += " --set position.fixed_position true"
    run(f"meshtastic {port_flag}{gps_flags}")

    if fixed is not None:
        # --setlat/--setlon are separate from --set: they write the node's own
        # position rather than a config field, and must land after
        # fixed_position is on or the firmware discards them.
        pos_flags = f" --setlat {fixed['lat']} --setlon {fixed['lon']}"
        if fixed["alt"] is not None:
            pos_flags += f" --setalt {fixed['alt']}"
        print(f"Provisioning surveyed fixed position: {fixed}")
        run(f"meshtastic {port_flag}{pos_flags}")

    # Reboot to apply changes
    run(f"meshtastic {port_flag} --reboot")


if __name__ == "__main__":
    main()