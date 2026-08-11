"""check_node_info.py  –  Meshtastic node & mesh inspector
==========================================================
Connects to a node over serial and reports, in one pass:

  1. Local node        – identity, role, firmware, telemetry (battery, channel
                          & air utilization, uptime), position, SNR.
  2. Device config      – every setting the configure scripts touch, read back
                          from the node (device/role, BLE, LoRa, GPS + intervals,
                          telemetry intervals, serial module). Role-agnostic:
                          works for sensor, gateway and PBX nodes alike.
  3. Radio health-check – region / preset / rebroadcast / channels / PSKs /
                          position_precision compared against
                          common/radio_config.py. A mismatch here means this
                          node cannot talk to the rest of the mesh (or, for
                          position_precision, that it is publishing coarsened
                          coordinates). PSKs are compared but never printed.
  4. Mesh node database – every node this device has heard (iface.nodes),
                          cross-checked against mesh_config.json: known nodes
                          are labelled (1/2/3/p1/p2) and their broadcast role
                          is compared to the expected device_role; expected
                          nodes that are absent and unknown foreign nodes are
                          flagged.

Exit code is 1 when any drift / mismatch is found, 0 when the node is healthy,
so the tool doubles as a CI / smoke health-check.

Usage:
    python src/tools/check_node_info.py                    # auto-detect port
    python src/tools/check_node_info.py --port /dev/ttyACM0
    python src/tools/check_node_info.py --no-mesh          # skip the mesh dump
"""
import argparse
import base64
import json
import sys
import time
from pathlib import Path

import meshtastic
import meshtastic.serial_interface

# Make the `src/` package root importable when run directly, mirroring the
# configuration scripts (src/*/configure.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import radio_config  # noqa: E402

# mesh_config.json lives at the repo root (this file is at src/tools/…), so
# resolve it relative to the file, not the current working directory.
MESH_CONFIG_PATH = Path(__file__).resolve().parents[2] / "mesh_config.json"

# ── Small formatting helpers ──────────────────────────────────────────────────


def enum_name(msg, field: str) -> str:
    """Return the symbolic name of a protobuf enum field via reflection.

    Reflection keeps this working across meshtastic library versions without
    importing config_pb2 (whose module path has moved between releases).
    """
    try:
        value = getattr(msg, field)
        enum_type = msg.DESCRIPTOR.fields_by_name[field].enum_type
        return enum_type.values_by_number[value].name
    except Exception:
        return str(getattr(msg, field, "?"))


def human_age(last_heard) -> str:
    """Render a lastHeard epoch (seconds) as a compact age like '3m ago'."""
    if not last_heard:
        return "never"
    delta = max(0, int(time.time()) - int(last_heard))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def decode_psk(psk_b64: str) -> bytes:
    """Decode a channel PSK as stored in .env: 'base64:<key>' or bare base64.

    Mirrors pbx/configure.py::decode_psk so the health-check compares against
    exactly what the configuration scripts write to the device.
    """
    if psk_b64 and psk_b64.startswith("base64:"):
        psk_b64 = psk_b64[len("base64:"):]
    return base64.b64decode(psk_b64)


def trailing_zero_bits(deg: float) -> int:
    """Count the zero low bits of a coordinate in Meshtastic's int32 encoding.

    Positions travel as int32 in units of 1e-7 degrees, and position_precision
    keeps only the top N bits — so a precision-reduced coordinate is an exact
    multiple of 2**(32-N) and the mask is visible in the value itself. A
    full-precision GPS reading has effectively random low bits.
    """
    units = round(abs(deg) * 1e7)
    if units == 0:                       # 0.0 is "no fix", not "quantized"
        return 0
    return (units & -units).bit_length() - 1


def fmt(value, unit: str = "") -> str:
    """Format an optional metric, falling back to '?' when absent."""
    if value is None:
        return "?"
    return f"{value}{unit}"


def field(msg, name: str):
    """Read a protobuf field, returning 'n/a' if it doesn't exist in this
    firmware/library version (field sets drift across releases)."""
    try:
        return getattr(msg, name)
    except Exception:
        return "n/a"


# ── Port resolution ───────────────────────────────────────────────────────────


def resolve_port(explicit_port):
    """Return the serial port to use, auto-detecting when not given.

    Exits with a clear message when zero or several Meshtastic devices are
    found and no --port was supplied (we won't guess between candidates).
    """
    if explicit_port:
        return explicit_port
    try:
        from meshtastic.util import findPorts
        ports = findPorts()
    except Exception as exc:
        print(f"ERROR: could not auto-detect a port ({exc}). Pass --port explicitly.")
        sys.exit(2)
    if not ports:
        print("ERROR: no Meshtastic device found. Plug one in or pass --port.")
        sys.exit(2)
    if len(ports) > 1:
        print("Multiple Meshtastic devices found; pass --port to choose one:")
        for p in ports:
            print(f"  {p}")
        sys.exit(2)
    print(f"Auto-detected port: {ports[0]}")
    return ports[0]


# ── Section 1: local node ───────────────────────────────────────────────────


def print_local_node(iface, info: dict, issues: list) -> str:
    """Print identity + telemetry of the connected node; return its node id.

    Appends to `issues` when the position looks grid-snapped. Note this is the
    node's OWN position, taken from its GPS — the precision mask is applied when
    a position is transmitted, so a clean reading here is NOT proof the setting
    works. The over-the-air evidence is in the remote entries of the node DB
    (see cross_check_mesh), which is where the quantization was first seen.
    """
    user = info.get("user", {}) or {}
    metrics = info.get("deviceMetrics", {}) or {}
    node_id = user.get("id", "?")

    print("=== Local node ===")
    print(f"  Node number : {info.get('num', '?')}")
    print(f"  Node ID     : {node_id}")
    print(f"  Long name   : {user.get('longName', '?')}")
    print(f"  Short name  : {user.get('shortName', '?')}")
    print(f"  Hardware    : {user.get('hwModel', '?')}")
    # role is omitted from NodeInfo when it is the default CLIENT(0).
    print(f"  Role        : {user.get('role', 'CLIENT')}")

    # Firmware version comes from device metadata, fetched at connect time.
    fw = "?"
    try:
        fw = iface.metadata.firmware_version or "?"
    except Exception:
        pass
    print(f"  Firmware    : {fw}")

    print("  Telemetry:")
    print(f"    Battery            : {fmt(metrics.get('batteryLevel'), '%')}")
    print(f"    Voltage            : {fmt(round(metrics['voltage'], 2) if metrics.get('voltage') is not None else None, ' V')}")
    print(f"    Channel util       : {fmt(round(metrics['channelUtilization'], 1) if metrics.get('channelUtilization') is not None else None, '%')}")
    print(f"    Air util (TX)      : {fmt(round(metrics['airUtilTx'], 1) if metrics.get('airUtilTx') is not None else None, '%')}")
    uptime = metrics.get("uptimeSeconds")
    print(f"    Uptime             : {fmt(uptime // 3600 if uptime is not None else None, 'h') if uptime else fmt(uptime)}")
    print(f"    SNR (last rx)      : {fmt(info.get('snr'), ' dB')}")

    pos = info.get("position", {}) or {}
    if pos.get("latitude") is not None and pos.get("longitude") is not None:
        print(f"    Position           : {pos['latitude']:.7f}, {pos['longitude']:.7f}"
              f" (alt {fmt(pos.get('altitude'), ' m')})")
        # Both coordinates snapped to the same power-of-two grid is a mask, not
        # a coincidence: 8 shared zero low bits happen by chance once in ~65k.
        tz = min(trailing_zero_bits(pos["latitude"]), trailing_zero_bits(pos["longitude"]))
        if tz >= 8:
            grid_km = (1 << tz) * 1e-7 * 111.32
            print(f"    !! quantized to a 2**{tz} grid (~{grid_km:.2f} km) "
                  f"-> effective precision {32 - tz}, expected "
                  f"{radio_config.POSITION_PRECISION}")
            issues.append(
                f"position: reported coordinates are quantized to ~{grid_km:.2f} km "
                f"(effective precision {32 - tz}, expected "
                f"{radio_config.POSITION_PRECISION})")
    else:
        print("    Position           : (no fix)")
    print()
    return node_id


# ── Section 2: full on-device configuration (role-agnostic) ──────────────────


def print_device_config(iface) -> None:
    """Dump every setting the configuration scripts touch, as read back from
    the node — so any node (sensor / gateway / PBX) can be verified against
    what it was meant to be. Pure reporting: the expected values are
    role-specific (they live in each role's configure_params.py), so this
    section does not judge, it just shows the on-device truth.
    """
    print("=== Device configuration (as read from the node) ===")

    def show(label, value):
        print(f"  {label:<32}: {value}")

    try:
        cfg = iface.localNode.localConfig
    except Exception as exc:
        print(f"  Could not read localConfig: {exc}\n")
        return

    # device: role + rebroadcast (rebroadcast is a no-op for CLIENT_MUTE, but
    # we still show whatever the device reports).
    show("device.role", enum_name(cfg.device, "role"))
    show("device.rebroadcast_mode", enum_name(cfg.device, "rebroadcast_mode"))

    # LoRa: the mesh-wide trio (also health-checked below against radio_config).
    show("lora.region", enum_name(cfg.lora, "region"))
    show("lora.modem_preset", enum_name(cfg.lora, "modem_preset"))
    show("lora.hop_limit", field(cfg.lora, "hop_limit"))
    # SX126x-only; on an SX127x T-Beam this is stored but ignored (reported,
    # not health-checked, so those nodes don't show a false mismatch).
    show("lora.sx126x_rx_boosted_gain", field(cfg.lora, "sx126x_rx_boosted_gain"))

    # bluetooth: proxies disable it (nRF52840 serves BLE); others use default.
    show("bluetooth.enabled", field(cfg.bluetooth, "enabled"))

    # position / GPS: mode + both intervals.
    show("position.gps_mode", enum_name(cfg.position, "gps_mode"))
    show("position.gps_update_interval", fmt(field(cfg.position, "gps_update_interval"), " s"))
    show("position.position_broadcast_secs", fmt(field(cfg.position, "position_broadcast_secs"), " s"))

    # Telemetry + serial live in moduleConfig, not localConfig.
    try:
        mod = iface.localNode.moduleConfig
        show("telemetry.device_update_interval",
             fmt(field(mod.telemetry, "device_update_interval"), " s"))
        show("telemetry.environment_update_interval",
             fmt(field(mod.telemetry, "environment_update_interval"), " s"))
        show("telemetry.environment_measurement_enabled",
             field(mod.telemetry, "environment_measurement_enabled"))
        # Serial module: only meaningful on the PBX-attached nodes (Stream
        # API on UART1), but shown for every node for completeness.
        show("serial.enabled", field(mod.serial, "enabled"))
        show("serial.mode", enum_name(mod.serial, "mode"))
        show("serial.txd", field(mod.serial, "txd"))
        show("serial.rxd", field(mod.serial, "rxd"))
        show("serial.baud", enum_name(mod.serial, "baud"))
        show("serial.timeout", field(mod.serial, "timeout"))
    except Exception as exc:
        print(f"  Could not read moduleConfig: {exc}")
    print()


# ── Section 3: radio health-check vs common/radio_config.py ──────────────────


def radio_health_check(iface) -> list:
    """Compare on-device radio settings against radio_config; return issues."""
    print("=== Radio health-check (vs common/radio_config.py) ===")
    issues = []

    def check(label, expected, actual):
        ok = expected == actual
        mark = "OK " if ok else "MISMATCH"
        # Never print raw PSK bytes; the caller passes a redacted 'actual'.
        print(f"  [{mark}] {label}: expected={expected} actual={actual}")
        if not ok:
            issues.append(f"radio: {label} (expected {expected}, got {actual})")

    try:
        cfg = iface.localNode.localConfig
    except Exception as exc:
        print(f"  Could not read localConfig: {exc}")
        return ["radio: localConfig unreadable"]

    check("region", radio_config.LORA_REGION, enum_name(cfg.lora, "region"))
    check("modem_preset", radio_config.LORA_PRESET, enum_name(cfg.lora, "modem_preset"))
    check("rebroadcast_mode", radio_config.REBROADCAST_MODE,
          enum_name(cfg.device, "rebroadcast_mode"))

    # Channels: name + PSK, by index. PSK is compared as bytes but reported
    # only as a match/mismatch verdict so the secret never hits the console.
    try:
        channels = iface.localNode.channels
    except Exception as exc:
        print(f"  Could not read channels: {exc}")
        issues.append("radio: channels unreadable")
        print()
        return issues

    expected_channels = [
        (radio_config.CHANNEL_TELEMETRY_IDX, radio_config.CHANNEL_TELEMETRY_NAME,
         radio_config.CHANNEL_TELEMETRY_PSK_B64),
        (radio_config.CHANNEL_MSG_IDX, radio_config.CHANNEL_MSG_NAME,
         radio_config.CHANNEL_MSG_PSK_B64),
    ]
    for idx, exp_name, exp_psk_b64 in expected_channels:
        if idx >= len(channels):
            print(f"  [MISMATCH] channel {idx}: missing on device (expected '{exp_name}')")
            issues.append(f"radio: channel {idx} missing (expected '{exp_name}')")
            continue
        settings = channels[idx].settings
        check(f"channel {idx} name", exp_name, settings.name)
        try:
            psk_ok = bytes(settings.psk) == decode_psk(exp_psk_b64)
        except Exception as exc:
            psk_ok = False
            print(f"  [MISMATCH] channel {idx} PSK: could not decode expected ({exc})")
        verdict = "OK " if psk_ok else "MISMATCH"
        print(f"  [{verdict}] channel {idx} PSK: {'matches .env' if psk_ok else 'differs from .env'}")
        if not psk_ok:
            issues.append(f"radio: channel {idx} PSK differs from .env")
        # Per-channel and applied by the SENDER, so it has to be right on every
        # device — a single unprovisioned radio coarsens its own positions no
        # matter how the rest of the mesh is set. Reading it back is the only
        # way to confirm it: `--info` omits the field while it holds the
        # firmware default, so absence there means "unset", not "full".
        check(f"channel {idx} position_precision", radio_config.POSITION_PRECISION,
              settings.module_settings.position_precision)
    print()
    return issues


# ── Section 4: mesh node DB cross-checked against mesh_config.json ───────────


def load_mesh_config() -> dict:
    """Load mesh_config.json; return {} (with a warning) if unreadable."""
    try:
        with open(MESH_CONFIG_PATH) as f:
            return json.load(f).get("nodes_cfg", {})
    except Exception as exc:
        print(f"  Warning: could not read {MESH_CONFIG_PATH.name}: {exc}")
        return {}


def cross_check_mesh(iface, local_node_id: str) -> list:
    """Dump the mesh node DB and cross-check it against mesh_config.json."""
    print("=== Mesh node database (cross-checked vs mesh_config.json) ===")
    issues = []
    nodes_cfg = load_mesh_config()
    # id -> logical label ("1", "p2", …) and expected role, for quick lookup.
    id_to_label = {v["id"]: k for k, v in nodes_cfg.items()}
    expected_role = {v["id"]: v.get("device_role", "CLIENT") for v in nodes_cfg.values()}

    nodes = getattr(iface, "nodes", None) or {}
    seen_ids = set()

    header = f"  {'label':<6} {'id':<11} {'role':<12} {'hops':>4} {'batt':>5} {'snr':>7} {'last heard':>12}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for nid, node in sorted(nodes.items(), key=lambda kv: (kv[1].get("hopsAway", 99), kv[0])):
        user = node.get("user", {}) or {}
        metrics = node.get("deviceMetrics", {}) or {}
        seen_ids.add(nid)
        label = id_to_label.get(nid, "-")
        if nid == local_node_id and label == "-":
            label = "self"
        role = user.get("role", "CLIENT")
        batt = metrics.get("batteryLevel")
        print(f"  {label:<6} {nid:<11} {role:<12} {fmt(node.get('hopsAway')):>4}"
              f" {fmt(batt, '%'):>5} {fmt(node.get('snr'), 'dB'):>7} {human_age(node.get('lastHeard')):>12}")

        # Role drift: a known node whose broadcast role differs from the plan.
        if nid in expected_role and role != expected_role[nid]:
            issues.append(f"mesh: {label} ({nid}) role is '{role}', expected '{expected_role[nid]}'")

        # Position quantization, checked here rather than only on the local node:
        # these coordinates arrived over the air, so they have been through the
        # sender's precision mask. This is the reading that exposes a node still
        # running a reduced position_precision — the local node's own GPS value
        # is pre-mask and looks fine either way.
        remote_pos = node.get("position", {}) or {}
        lat, lon = remote_pos.get("latitude"), remote_pos.get("longitude")
        if nid != local_node_id and lat is not None and lon is not None:
            tz = min(trailing_zero_bits(lat), trailing_zero_bits(lon))
            if tz >= 8:
                grid_km = (1 << tz) * 1e-7 * 111.32
                issues.append(
                    f"position: {label} ({nid}) transmits coordinates quantized to "
                    f"~{grid_km:.2f} km (effective precision {32 - tz}, expected "
                    f"{radio_config.POSITION_PRECISION}) — reprovision that node")

    # Expected-but-absent nodes (offline, out of range, or never heard).
    for nid, label in id_to_label.items():
        if nid not in seen_ids:
            issues.append(f"mesh: expected node {label} ({nid}) not present in the DB")
    # Foreign nodes not in the plan (skip the local node itself).
    for nid in seen_ids:
        if nid not in id_to_label and nid != local_node_id:
            issues.append(f"mesh: unknown node {nid} present (not in mesh_config.json)")
    print()

    # Local-only cross-check: hop_limit is not broadcast, so it is only
    # verifiable for the node we are connected to, via its local config.
    if local_node_id in nodes_cfg:
        label = id_to_label[local_node_id]
        expected = nodes_cfg[local_node_id]
        try:
            actual_hop = iface.localNode.localConfig.lora.hop_limit
            if actual_hop != expected.get("hop_limit"):
                issues.append(
                    f"mesh: local node {label} hop_limit is {actual_hop}, "
                    f"expected {expected.get('hop_limit')}")
            actual_role = enum_name(iface.localNode.localConfig.device, "role")
            if actual_role != expected.get("device_role"):
                issues.append(
                    f"mesh: local node {label} role is {actual_role}, "
                    f"expected {expected.get('device_role')}")
        except Exception as exc:
            print(f"  Could not verify local hop_limit/role: {exc}")
    return issues


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a Meshtastic node and cross-check the mesh against "
                    "mesh_config.json and common/radio_config.py.")
    parser.add_argument("--port", help="Serial port (e.g. /dev/ttyACM0). "
                                       "Auto-detected when omitted.")
    parser.add_argument("--no-mesh", action="store_true",
                        help="Skip the mesh node-database dump / cross-check.")
    args = parser.parse_args()

    port = resolve_port(args.port)

    try:
        iface = meshtastic.serial_interface.SerialInterface(port)
    except Exception as exc:
        print(f"ERROR: could not open {port}: {exc}")
        return 2

    issues = []
    try:
        info = iface.getMyNodeInfo()
        local_node_id = print_local_node(iface, info, issues)
        print_device_config(iface)
        issues += radio_health_check(iface)
        if not args.no_mesh:
            issues += cross_check_mesh(iface, local_node_id)
    finally:
        iface.close()

    if issues:
        print("=== Issues found ===")
        for issue in issues:
            print(f"  ! {issue}")
        print(f"\n{len(issues)} issue(s) detected.")
        return 1

    print("=== Node healthy: no drift or mismatch detected. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
