import argparse
import base64
import subprocess
import sys
import time
from pathlib import Path

import meshtastic.serial_interface

# Make the `src/` package root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pbx import configure_params as node_params  # noqa: E402
from common import mesh_config  # noqa: E402
from common.meshtastic_cli import get_config_value  # noqa: E402

# Resolve relative to repo root (this file lives at <root>/src/pbx/configure.py),
# so it works regardless of the current working directory.
MESH_CONFIG_PATH = str(Path(__file__).resolve().parents[2] / "mesh_config.json")

MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds to wait before retrying
SETTLE_DELAY = 10  # seconds to let the node settle after a successful command

# Keywords that indicate a transient serial error worth retrying
RETRYABLE_ERRORS = [
    "couldn't be opened",
    "Input/output error",
    "OS Error",
    "serial device",
    "write failed",
]

# Channels this node must join: telemetry (primary) + messaging, shared
# mesh-wide via common/radio_config.py. PSKs come from .env, same source of
# truth as node/ and gateway/.
CHANNELS = [
    (node_params.CHANNEL_TELEMETRY_IDX,
     node_params.CHANNEL_TELEMETRY_NAME,
     node_params.CHANNEL_TELEMETRY_PSK_B64),
    (node_params.CHANNEL_MSG_IDX,
     node_params.CHANNEL_MSG_NAME,
     node_params.CHANNEL_MSG_PSK_B64),
]


def is_retryable(stderr: str, stdout: str) -> bool:
    combined = (stderr + stdout).lower()
    return any(keyword.lower() in combined for keyword in RETRYABLE_ERRORS)


def run(cmd, retries=MAX_RETRIES) -> bool:
    """Run a meshtastic CLI command given as an argv list (no shell).

    Returns True on success, False if it still failed after `retries`
    attempts. Transient serial errors (RETRYABLE_ERRORS) are retried.
    """
    print(f"\nRunning: {' '.join(cmd)}")
    for attempt in range(1, retries + 1):
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode == 0 and not is_retryable(result.stderr, result.stdout):
            time.sleep(SETTLE_DELAY)
            return True
        # Something went wrong.
        print(f"ERROR (attempt {attempt}/{retries}):", result.stderr or result.stdout)
        if attempt < retries:
            print(f"Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
        else:
            print(f"Command failed after {retries} attempts.")
            time.sleep(2)
    return False


def decode_psk(psk_b64: str) -> bytes:
    """Decode a channel PSK as stored in .env: 'base64:<key>' or bare base64."""
    if psk_b64.startswith("base64:"):
        psk_b64 = psk_b64[len("base64:"):]
    return base64.b64decode(psk_b64)


def load_config(node_id):
    """Returns (node_cfg, intervals) for one PBX node from mesh_config.json."""
    data = mesh_config.load(MESH_CONFIG_PATH)
    cfg  = mesh_config.node_cfg(data, node_id)
    return cfg, mesh_config.intervals_for(cfg)


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure the Meshtastic node attached to the PBX.")
    parser.add_argument("--node-id", required=True, help="PBX node entry in mesh_config.json (e.g. 'p1', 'p2')")
    parser.add_argument("--port", required=True, help="Serial port of the device (e.g. /dev/ttyUSB0)")
    args = parser.parse_args()

    # Per-PBX settings (p1 and p2 have different hop limits) live in
    # mesh_config.json, same registry as the sensing nodes.
    node_cfg, intervals = load_config(args.node_id)
    hop_limit = node_cfg["hop_limit"]
    device_role = node_cfg["device_role"]

    # Broadcast cadences live in mesh_config.json, the same numbers the gateway's
    # PDR estimator measures against. A kind omitted there is left untouched on
    # the radio and untracked by the gateway.
    print(f"Broadcast cadences from mesh_config.json: {intervals}")

    # radio_config already enforces the telemetry PSK; the PBX node also
    # needs the messaging channel key, so fail early if it is missing.
    if not node_params.CHANNEL_MSG_PSK_B64:
        print(
            "ERROR: LORA_MSG_CHANNEL_PSK is not set. Copy .env.example to .env "
            "and set the messaging-channel PSK (shared mesh-wide)."
        )
        return 2

    # Base argv for every meshtastic CLI invocation (no shell → no quoting issues).
    mesh = ["meshtastic", "--port", args.port]

    failures = []

    def step(label, cmd):
        """Run a CLI step and record the label if it ultimately fails."""
        if not run(cmd):
            failures.append(label)

    print(f"Starting PBX-node configuration of '{args.node_id}' on {args.port}...")

    # LoRa config: region, preset, hop limit. sx126x_rx_boosted_gain is
    # honoured only by SX126x radios; on an SX127x T-Beam it is stored but
    # ignored (harmless), so we set it mesh-wide from radio_config.
    step("LoRa (region/preset/hop_limit)", mesh + [
        "--set", "lora.region", node_params.LORA_REGION,
        "--set", "lora.modem_preset", node_params.LORA_PRESET,
        "--set", "lora.hop_limit", str(hop_limit),
        "--set", "lora.sx126x_rx_boosted_gain", str(node_params.SX126X_RX_BOOSTED_GAIN).lower(),
    ])

    # Device config: role then rebroadcast mode, chained in ONE invocation so
    # the CLI applies both in a single config write. Role is ordered first;
    # rebroadcast is verified afterwards, so a dropped write (role reboots) is
    # still caught by the check below.
    step("device role + rebroadcast_mode", mesh + [
        "--set", "device.role", device_role,
        "--set", "device.rebroadcast_mode", node_params.REBROADCAST_MODE,
    ])
    actual = get_config_value(mesh, "device.rebroadcast_mode")
    if node_params.REBROADCAST_MODE.lower() not in actual.lower():
        print(f"WARNING: device.rebroadcast_mode did not persist "
              f"(expected {node_params.REBROADCAST_MODE}, read '{actual or 'unknown'}').")
        failures.append("device.rebroadcast_mode (verify)")
    else:
        print(f"Verified device.rebroadcast_mode = {node_params.REBROADCAST_MODE}")

    # Bluetooth config: off — the nRF52840 PBX serves the BLE side.
    ble = str(node_params.BLUETOOTH_ENABLE).lower()
    step("bluetooth", mesh + ["--set", "bluetooth.enabled", ble])

    # Channel 0 (primary) already exists — rename it to the telemetry channel.
    # Channel 1 (messaging) must be created with --ch-add; re-running after it
    # exists makes the step fail, which is reported but harmless.
    step(f"channel {node_params.CHANNEL_TELEMETRY_IDX} rename", mesh + [
        "--ch-index", str(node_params.CHANNEL_TELEMETRY_IDX),
        "--ch-set", "name", node_params.CHANNEL_TELEMETRY_NAME,
    ])
    step(f"channel {node_params.CHANNEL_MSG_IDX} add", mesh + [
        "--ch-add", node_params.CHANNEL_MSG_NAME,
    ])

    # Set PSKs via Python API — the CLI assigns the value as a str to a bytes
    # field, causing "expected bytes, str found". The API accepts bytes directly.
    try:
        iface = meshtastic.serial_interface.SerialInterface(devPath=args.port)
        try:
            for ch_idx, ch_name, psk_b64 in CHANNELS:
                iface.localNode.channels[ch_idx].settings.psk = decode_psk(psk_b64)
                iface.localNode.writeChannel(ch_idx)
                print(f"PSK set for channel {ch_idx} ({ch_name}).")
                time.sleep(5)
        finally:
            iface.close()
    except Exception as exc:
        print(f"ERROR setting PSKs via API: {exc}")
        failures.append("channel PSKs (API)")

    # Telemetry config
    dev_meas = str(node_params.TELEMETRY_DEV_MEAS_ENABLED).lower()
    telemetry_flags = ["--set", "telemetry.device_telemetry_enabled", dev_meas]
    if node_params.TELEMETRY_DEV_MEAS_ENABLED and "device" in intervals:
        telemetry_flags += ["--set", "telemetry.device_update_interval",
                            str(intervals["device"])]
    step("telemetry", mesh + telemetry_flags)

    # Serial module config: Stream API on UART1 → the PBX drives the node.
    serial_en = str(node_params.SERIAL_MODULE_ENABLE).lower()
    if node_params.SERIAL_MODULE_ENABLE:
        step("serial module", mesh + [
            "--set", "serial.enabled", serial_en,
            "--set", "serial.mode", node_params.SERIAL_MODULE_MODE,
            "--set", "serial.txd", str(node_params.SERIAL_MODULE_TXD),
            "--set", "serial.rxd", str(node_params.SERIAL_MODULE_RXD),
            "--set", "serial.baud", node_params.SERIAL_MODULE_BAUDRATE,
            "--set", "serial.timeout", str(node_params.SERIAL_MODULE_TIMEOUT),
        ])
    else:
        step("serial module", mesh + ["--set", "serial.enabled", serial_en])

    # GPS config. position_broadcast_smart_enabled is set explicitly (firmware
    # default is true): movement-triggered extra position packets would break the
    # fixed-cadence assumption behind the gateway's PDR estimator.
    gps_flags = [
        "--set", "position.gps_mode", node_params.GPS_MODE,
        "--set", "position.gps_update_interval", str(node_params.GPS_UPDATE_INTERNAL_INTERVAL),
        "--set", "position.position_broadcast_smart_enabled",
        str(node_params.POSITION_BROADCAST_SMART_ENABLED).lower(),
    ]
    if "position" in intervals:
        gps_flags += ["--set", "position.position_broadcast_secs", str(intervals["position"])]

    # A surveyed position from mesh_config.json is provisioned as the node's fixed
    # position: for a stationary node it does not drift, does not need sky view,
    # and is accurate enough to compute inter-node distances from. The node keeps
    # broadcasting it on the position cadence, so the PDR flow is unaffected.
    #
    # NOTE: a reduced `module_settings.position_precision` on the CHANNEL
    # quantises lat/lon on transmission, so a fixed position is rounded onto a
    # grid exactly as a GPS fix is. Check that first or exact coordinates go in
    # and identical ones come out.
    fixed = mesh_config.position_for(node_cfg)
    if fixed is not None:
        gps_flags += ["--set", "position.fixed_position", "true"]
    step("GPS", mesh + gps_flags)

    if fixed is not None:
        # --setlat/--setlon write the node's own position rather than a config
        # field, and must run after fixed_position is on or they are discarded.
        pos_flags = ["--setlat", str(fixed["lat"]), "--setlon", str(fixed["lon"])]
        if fixed["alt"] is not None:
            pos_flags += ["--setalt", str(fixed["alt"])]
        print(f"Provisioning surveyed fixed position: {fixed}")
        step("fixed position", mesh + pos_flags)

    # Reboot to apply changes
    step("reboot", mesh + ["--reboot"])

    # Summary + exit status.
    if failures:
        print("\n=== Configuration completed with ERRORS ===")
        for label in failures:
            print(f"  FAILED: {label}")
        print(f"{len(failures)} step(s) failed; the node may be partially configured.")
        return 1

    print("\n=== Configuration completed successfully ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
