# Shared reader for mesh_config.json.
#
# Both the provisioning scripts (src/node/configure.py, src/pbx/configure.py)
# and the gateway receiver resolve broadcast cadences through this module, so the
# interval a node is *configured* with and the interval the gateway *measures*
# against can never drift apart — the same single-source rule that
# common/radio_config.py enforces for channel/region/preset.
#
# Schema:
#
#     {
#       "pdr_cfg": {
#         "sweep_interval_sec": 30,
#         "window_sec": 3600                  # or {"device": 3600, "position": 21600}
#       },
#       "nodes_cfg": {
#         "1": {"id": "!0b64122b", "hop_limit": 2, "device_role": "CLIENT_BASE",
#               "intervals": {"device": 120, "environment": 120, "position": 600}}
#       }
#     }
#
# `intervals` is AUTHORITATIVE AND COMPLETE per node — absent kinds are NOT
# filled in from defaults. A node that does not broadcast environment telemetry
# (the PBX-attached nodes don't) simply omits that key, and the gateway then
# tracks no PDR for that flow instead of measuring against a cadence the node
# never had. Defaults apply only when a node has no `intervals` block at all.
import json

# PDR flow kinds. One flow per (node, kind); each has its own cadence.
FLOW_KINDS = ("device", "environment", "position")

# Fallback cadences for nodes with no `intervals` block (sensing-node profile).
DEFAULT_INTERVALS = {"device": 120, "environment": 120, "position": 600}

DEFAULT_PDR_WINDOW_SEC     = 3600   # rolling-window length per flow [seconds]
DEFAULT_SWEEP_INTERVAL_SEC = 30     # how often silence is checked for [seconds]


def load(path: str) -> dict:
    """
    Reads and minimally validates mesh_config.json.

    Raises:
        FileNotFoundError, json.JSONDecodeError, ValueError
    """
    with open(path, "r") as f:
        data = json.load(f)
    if "nodes_cfg" not in data:
        raise ValueError(f"'nodes_cfg' key missing in {path}")
    return data


def node_cfg(data: dict, node_id: str) -> dict:
    """Returns the config block for one node key ('1', 'p1', ...)."""
    nodes = data["nodes_cfg"]
    if node_id not in nodes:
        raise ValueError(f"node '{node_id}' not found in mesh_config (have: {list(nodes)})")
    return nodes[node_id]


def intervals_for(cfg: dict) -> dict:
    """
    Broadcast cadence per flow kind for one node, in seconds.

    Returns only the kinds the node actually broadcasts. Falls back to
    DEFAULT_INTERVALS when the node declares no `intervals` block.
    """
    raw = cfg.get("intervals")
    if raw is None:
        return dict(DEFAULT_INTERVALS)

    out = {}
    for kind, secs in raw.items():
        if kind not in FLOW_KINDS:
            raise ValueError(f"unknown flow kind '{kind}' in intervals (expected {FLOW_KINDS})")
        if secs is None:            # explicit "this node does not broadcast it"
            continue
        if not isinstance(secs, int) or isinstance(secs, bool) or secs <= 0:
            raise ValueError(f"interval for '{kind}' must be a positive int, got {secs!r}")
        out[kind] = secs
    return out


def pdr_window_sec(data: dict) -> dict:
    """
    Rolling-window length per flow kind, in seconds.

    Accepts either a single int (applied to every kind) or a per-kind dict.
    Note the resolution trade-off: the window holds `window_sec / interval`
    slots, so 3600 s over a 600 s position cadence is only 6 samples — the
    receiver reports `pdr_window_slots` so a thin denominator is visible in the
    data rather than hidden. Raise the position window (e.g. 21600) for a
    statistically useful rolling figure.
    """
    raw = (data.get("pdr_cfg") or {}).get("window_sec", DEFAULT_PDR_WINDOW_SEC)
    if isinstance(raw, dict):
        return {k: int(raw.get(k, DEFAULT_PDR_WINDOW_SEC)) for k in FLOW_KINDS}
    return {k: int(raw) for k in FLOW_KINDS}


def sweep_interval_sec(data: dict) -> int:
    """How often the receiver checks quiet flows for un-charged losses."""
    return int((data.get("pdr_cfg") or {}).get(
        "sweep_interval_sec", DEFAULT_SWEEP_INTERVAL_SEC))
