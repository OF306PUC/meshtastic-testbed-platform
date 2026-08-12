"""configure_device.py  –  Configure the portable field-test gateway
=====================================================================
Configures a LILYGO board to act as the portable receiver used during
solar-node installation (see receiver.py). It joins the SAME telemetry channel
as the production mesh — reusing src/common/radio_config.py as the single source
of truth — so it can actually hear the nodes, and enables GPS so its own
position is logged for walk-tests. Role is CLIENT_MUTE (listen only, never
rebroadcast) and its own telemetry generation is disabled.

Requires .env at the repo root with the channel PSKs (same as production);
importing common.radio_config fails fast if they are missing.

Usage:
    python src/tools/field_testing/configure_device.py [--port /dev/ttyACM0]
"""
import sys
import argparse
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]   # src/
sys.path.insert(0, str(_SRC))

from common import radio_config as rc                    # noqa: E402
from common.meshtastic_cli import run, get_config_value  # noqa: E402

# ── Field-test-specific device settings (not mesh-wide radio config) ──────────
DEVICE_ROLE           = "CLIENT_MUTE"   # listen only; never rebroadcast
GPS_MODE              = "ENABLED"       # log the receiver's own position for walk-tests
GPS_UPDATE_INTERVAL_S = 30
GPS_BROADCAST_SECS    = 30


def main():
    parser = argparse.ArgumentParser(
        description="Configure the portable field-test gateway via meshtastic CLI"
    )
    parser.add_argument(
        "--port", type=str, default=None,
        help="Serial port of the device (e.g. /dev/ttyACM0)"
    )
    args = parser.parse_args()
    port_flag = f"--port {args.port}" if args.port else ""

    print("Configuring field-test gateway using meshtastic CLI...")

    # LoRa radio — must match the mesh (region / preset / boosted gain).
    run(
        f"meshtastic {port_flag} --set lora.region {rc.LORA_REGION}"
        f" --set lora.modem_preset {rc.LORA_PRESET}"
        f" --set lora.sx126x_rx_boosted_gain {str(rc.SX126X_RX_BOOSTED_GAIN).lower()}"
    )

    # Device role (listen-only) + rebroadcast mode, chained in one write; then
    # verify rebroadcast persisted (it can silently drop when set with role).
    run(
        f"meshtastic {port_flag} --set device.role {DEVICE_ROLE}"
        f" --set device.rebroadcast_mode {rc.REBROADCAST_MODE}"
    )
    mesh_argv = ["meshtastic"] + (["--port", args.port] if args.port else [])
    actual = get_config_value(mesh_argv, "device.rebroadcast_mode")
    if rc.REBROADCAST_MODE.lower() not in actual.lower():
        print(f"WARNING: device.rebroadcast_mode did not persist "
              f"(expected {rc.REBROADCAST_MODE}, read '{actual or 'unknown'}').")
    else:
        print(f"Verified device.rebroadcast_mode = {rc.REBROADCAST_MODE}")

    # Telemetry channel (index 0) — this is the channel that carries the nodes'
    # telemetry/position packets, so the receiver must join it to hear them.
    run(
        f'meshtastic {port_flag} --ch-set name "{rc.CHANNEL_TELEMETRY_NAME}" '
        f'--ch-set psk {rc.CHANNEL_TELEMETRY_PSK_B64} '
        f'--ch-set module_settings.position_precision {rc.POSITION_PRECISION} '
        f'--ch-index {rc.CHANNEL_TELEMETRY_IDX}'
    )

    # Messaging channel (index 1) — configured mesh-wide. The receiver is
    # CLIENT_MUTE (never relays), but joining PUC_NET keeps it a full mesh
    # member and lets it decode phone messages from the BLE proxies, matching
    # the production node/gateway config. Re-running --ch-add on an existing
    # channel just logs an error and continues.
    run(f'meshtastic {port_flag} --ch-add "{rc.CHANNEL_MSG_NAME}"')
    run(
        f'meshtastic {port_flag} --ch-set psk {rc.CHANNEL_MSG_PSK_B64} '
        f'--ch-set module_settings.position_precision {rc.POSITION_PRECISION} '
        f'--ch-index {rc.CHANNEL_MSG_IDX}'
    )

    # The receiver only relays/logs what it hears; disable its own telemetry.
    run(
        f"meshtastic {port_flag} --set telemetry.device_telemetry_enabled false"
        f" --set telemetry.environment_measurement_enabled false"
    )

    # GPS ON — log the receiver's own position for range/walk tests.
    run(
        f"meshtastic {port_flag} --set position.gps_mode {GPS_MODE}"
        f" --set position.gps_update_interval {GPS_UPDATE_INTERVAL_S}"
        f" --set position.position_broadcast_secs {GPS_BROADCAST_SECS}"
    )

    # Reboot to apply changes.
    run(f"meshtastic {port_flag} --reboot")


if __name__ == "__main__":
    main()
