"""
plot_history.py  –  LoRa TestBed historical analysis
=====================================================
Queries InfluxDB for the last N days and produces one PNG per node:

    temperature  |  humidity        (10-min MA)
    RSSI         |  SNR             (10-min MA)
    battery      |  voltage         (10-min MA)
    hops (step)  |  –

Usage:
    python plot_history.py                   # last 3 days, saves PNGs next to script
    python plot_history.py --days 7
    python plot_history.py --days 1 --show   # also open interactive window
    python plot_history.py --out /tmp/plots
"""

import argparse
import datetime
import os

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests

# ── InfluxDB connection ───────────────────────────────────────────────────────

INFLUX_URL  = os.environ.get("DB_HOST",     "http://localhost:8086")
INFLUX_DB   = os.environ.get("DB_NAME",     "cpsrtc_lora_telemetry")
INFLUX_USER = os.environ.get("DB_USERNAME", "admin")
INFLUX_PASS = os.environ.get("DB_PASSWORD", "admin")

if not INFLUX_URL.startswith("http"):
    INFLUX_URL = f"http://{INFLUX_URL}:8086"

# ── Style ─────────────────────────────────────────────────────────────────────

_PALETTE        = ["#2196F3", "#FF5722", "#4CAF50", "#FF9800", "#9C27B0"]
GAP_THRESHOLD_S = 120


# ── InfluxDB helpers ──────────────────────────────────────────────────────────

def _query(q: str) -> list[dict]:
    resp = requests.get(
        f"{INFLUX_URL}/query",
        params={"db": INFLUX_DB, "q": q, "epoch": "s"},
        auth=(INFLUX_USER, INFLUX_PASS),
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [{}])
    series  = results[0].get("series", [])
    if not series:
        return []
    cols   = series[0]["columns"]
    values = series[0].get("values", [])
    return [dict(zip(cols, row)) for row in values]


def fetch_nodes(days: int) -> list[str]:
    rows = _query(
        f"SHOW TAG VALUES FROM mqtt_consumer "
        f"WITH KEY = node_label WHERE time > now() - {days}d"
    )
    return sorted({r["value"] for r in rows})


def fetch_node_data(node_label: str, days: int) -> pd.DataFrame:
    rows = _query(
        f"SELECT * FROM mqtt_consumer "
        f"WHERE node_label = '{node_label}' AND time > now() - {days}d "
        f"ORDER BY time ASC"
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["time"] = _to_local(df["time"])
    df.sort_values("time", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ── Time helpers ──────────────────────────────────────────────────────────────

def _to_local(series: pd.Series) -> pd.Series:
    now = datetime.datetime.now().astimezone()
    return (
        pd.to_datetime(series, unit="s", utc=True)
        .dt.tz_convert(datetime.timezone(now.utcoffset()))
        .dt.tz_localize(None)
    )


def _tz_label() -> str:
    return datetime.datetime.now().astimezone().tzname()


def _session_str(times: pd.Series) -> str:
    t0, t1 = times.min(), times.max()
    dur = (t1 - t0).total_seconds()
    return (
        f"{t0.strftime('%Y-%m-%d  %H:%M')} – {t1.strftime('%Y-%m-%d %H:%M')}  "
        f"({int(dur // 3600)}h {int((dur % 3600) // 60):02d}m)"
    )


# ── Moving average ────────────────────────────────────────────────────────────

def _smooth(src: pd.DataFrame, col: str) -> pd.Series:
    """10-minute time-based rolling mean; index is the original datetime."""
    s = src.set_index("time")[col].dropna()
    if s.empty:
        return s
    return s.rolling("10min").mean()


# ── Axis helpers ──────────────────────────────────────────────────────────────

def _fmt_xaxis(ax, tz_label: str, show_label: bool = True):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=8)
    if show_label:
        ax.set_xlabel(f"Time ({tz_label})", fontsize=9)


def _find_gaps(times: pd.Series):
    diffs = times.diff()
    return [
        (times.iloc[i - 1], times.iloc[i])
        for i in range(1, len(times))
        if diffs.iloc[i].total_seconds() > GAP_THRESHOLD_S
    ]


def _shade_gaps(axes, gaps, colour):
    for start, end in gaps:
        for ax in axes:
            ax.axvspan(start, end, color=colour, alpha=0.08, linewidth=0)
            for t in (start, end):
                ax.axvline(t, color=colour, linewidth=0.6, linestyle="--", alpha=0.35)


# ── Per-node figure ───────────────────────────────────────────────────────────

def plot_node(label: str, df: pd.DataFrame, colour: str, tz_label: str, out_dir: str):
    # Split by topic
    env   = df[df["topic"].str.endswith("/environment")].copy() if "topic" in df else pd.DataFrame()
    telem = df[df["topic"].str.endswith("/device")].copy()      if "topic" in df else pd.DataFrame()

    if df.empty:
        print(f"[SKIP] No data for {label}")
        return

    fig, axes = plt.subplots(4, 2, figsize=(14, 14), sharex=True)
    ax_temp, ax_hum, ax_rssi, ax_snr, ax_bat, ax_volt, ax_hop, ax_empty = axes.flat
    ax_empty.set_visible(False)

    fig.suptitle(
        f"{label}  –  Historical Analysis\n{_session_str(df['time'])}",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.95], h_pad=3.5, w_pad=3.0)

    visible_axes = [ax for ax in axes.flat if ax.get_visible()]
    if not telem.empty:
        _shade_gaps(visible_axes, _find_gaps(telem["time"]), colour)

    def _plot(ax, src, col, title, ylabel, ylim=None):
        ax.set_title(title, fontsize=11)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.grid(True, linestyle="--", alpha=0.4)
        if ylim:
            ax.set_ylim(*ylim)
        if not src.empty and col in src.columns:
            valid = src[src[col].notna() & (src[col] != 0.0)]
            if not valid.empty:
                ax.plot(valid["time"], valid[col],
                        color=colour, linewidth=0.5, alpha=0.2, marker=".", markersize=2)
                ma = _smooth(valid, col)
                ax.plot(ma.index, ma.values, color=colour, linewidth=1.8)
            else:
                ax.text(0.5, 0.5, "no data", transform=ax.transAxes,
                        ha="center", va="center", color="grey", fontsize=10)

    def _step_plot(ax, src, col, title, ylabel):
        ax.set_title(title, fontsize=11)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
        if not src.empty and col in src.columns:
            valid = src[src[col].notna()]
            if not valid.empty:
                ax.step(valid["time"], valid[col],
                        color=colour, linewidth=1.5, where="post")
            else:
                ax.text(0.5, 0.5, "no data", transform=ax.transAxes,
                        ha="center", va="center", color="grey", fontsize=10)

    _plot(ax_temp, env,   "temperature",   "Temperature",       "°C")
    _plot(ax_hum,  env,   "humidity",      "Relative Humidity", "%",   (0, 100))
    _plot(ax_rssi, telem, "rssi",          "RSSI",              "dBm")
    _plot(ax_snr,  telem, "snr",           "SNR",               "dB")
    _plot(ax_bat,  telem, "battery_level", "Battery Level",     "%",   (0, 100))
    _plot(ax_volt, telem, "voltage",       "Voltage",           "V")
    _step_plot(ax_hop, telem, "hop",       "Hops to Gateway",   "hops")

    for ax in axes.flat:
        if ax.get_visible():
            _fmt_xaxis(ax, tz_label, show_label=(ax in axes[-1]))

    out = os.path.join(out_dir, f"{label}_history.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved → {out}")


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(nodes: list[str], data: dict[str, pd.DataFrame], days: int):
    print(f"\n── Last {days} days ──────────────────────────────────────────────────")
    for label in nodes:
        df = data[label]
        if df.empty:
            print(f"  {label}  |  no data")
            continue
        n_env   = len(df[df["topic"].str.endswith("/environment")]) if "topic" in df else 0
        n_dev   = len(df[df["topic"].str.endswith("/device")])      if "topic" in df else 0
        rssi_str = ""
        if "rssi" in df.columns:
            r = df["rssi"].dropna()
            r = r[r != 0.0]
            if not r.empty:
                rssi_str = f"  |  RSSI {r.min():.0f} – {r.max():.0f} dBm"
        print(
            f"  {label}  |  {n_dev} device, {n_env} env packets"
            f"{rssi_str}"
            f"  |  {_session_str(df['time'])}"
        )
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Plot LoRa TestBed historical data from InfluxDB")
    parser.add_argument("--days",  type=int, default=3,  help="How many days back to query (default: 3)")
    parser.add_argument("--out",   default=".",          help="Output directory for PNG files (default: .)")
    parser.add_argument("--show",  action="store_true",  help="Open interactive matplotlib window after saving")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    tz_label = _tz_label()

    print(f"Querying InfluxDB at {INFLUX_URL} / db={INFLUX_DB} ...")
    nodes = fetch_nodes(args.days)
    if not nodes:
        print(f"No nodes found in the last {args.days} days.")
        return

    print(f"Found nodes: {nodes}\n")
    data = {label: fetch_node_data(label, args.days) for label in nodes}

    print_summary(nodes, data, args.days)

    for i, label in enumerate(nodes):
        plot_node(label, data[label], _PALETTE[i % len(_PALETTE)], tz_label, args.out)

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
