"""
plot_data.py  –  LoRa TestBed session analysis
===============================================
One figure per remote node:
    temperature  |  humidity
    channel util |  air util TX
    RSSI         |  SNR

One figure for the gateway:
    latitude vs time  |  longitude vs time
    altitude vs time  |  trajectory (lon vs lat, coloured by time)

Usage:
    python plot_data.py                      # looks in ./data
    python plot_data.py --data-dir data/run2
"""

import argparse
import datetime
import glob
import os
import re

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ── Config ────────────────────────────────────────────────────────────────────
_PALETTE          = ["#2196F3", "#FF5722", "#4CAF50", "#FF9800", "#9C27B0"]
_GATEWAY_COLOUR   = "#607D8B"
GAP_THRESHOLD_S   = 120   # gap above this → shade as no-coverage

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

# ── Data loading ──────────────────────────────────────────────────────────────

def _load_csvs(data_dir: str, pattern: str, ts_col: str) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(data_dir, pattern)))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["time"] = _to_local(df[ts_col])
    df.sort_values("time", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def _discover_nodes(data_dir: str) -> list:
    """Return sorted node labels inferred from *_telemetry_*.csv filenames."""
    files = glob.glob(os.path.join(data_dir, "*_telemetry_*.csv"))
    labels = sorted({
        re.match(r".*[/\\](.+)_telemetry_", f).group(1)
        for f in files
    })
    return labels

# ── Axis formatting ───────────────────────────────────────────────────────────

def _fmt_xaxis(ax, tz_label: str, show_label: bool = True):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
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
            ax.axvspan(start, end, color=colour, alpha=0.10, linewidth=0)
            for t in (start, end):
                ax.axvline(t, color=colour, linewidth=0.7, linestyle="--", alpha=0.4)

def _session_str(times: pd.Series) -> str:
    t0, t1 = times.min(), times.max()
    dur = (t1 - t0).total_seconds()
    return (
        f"{t0.strftime('%Y-%m-%d  %H:%M')} – {t1.strftime('%H:%M')}  "
        f"({int(dur // 60)} min {int(dur % 60):02d} s)"
    )

# ── Node figure ───────────────────────────────────────────────────────────────

def plot_node(label: str, data_dir: str, colour: str, tz_label: str):
    telem = _load_csvs(data_dir, f"{label}_telemetry_*.csv", "received_at")
    env   = _load_csvs(data_dir, f"{label}_env_*.csv",       "received_at")

    if telem.empty and env.empty:
        print(f"[SKIP] No data for {label}")
        return

    all_times = pd.concat([
        telem["time"] if not telem.empty else pd.Series(dtype="datetime64[ns]"),
        env["time"]   if not env.empty   else pd.Series(dtype="datetime64[ns]"),
    ])

    fig, axes = plt.subplots(3, 2, figsize=(13, 11), sharex=True)
    ax_temp, ax_hum, ax_ch, ax_air, ax_rssi, ax_snr = axes.flat

    fig.suptitle(
        f"{label}  –  Session Analysis\n{_session_str(all_times)}",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.95], h_pad=3.5, w_pad=3.0)

    all_axes = list(axes.flat)
    if not telem.empty:
        _shade_gaps(all_axes, _find_gaps(telem["time"]), colour)

    def _plot(ax, df, col, title, ylabel, ylim=None):
        ax.set_title(title, fontsize=11)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.grid(True, linestyle="--", alpha=0.4)
        if ylim:
            ax.set_ylim(*ylim)
        if not df.empty and col in df.columns:
            ax.plot(df["time"], df[col],
                    color=colour, linewidth=1.5, marker=".", markersize=4)

    _plot(ax_temp, env,   "temperature", "Temperature",        "°C")
    _plot(ax_hum,  env,   "humidity",    "Relative Humidity",  "%",   (0, 100))
    _plot(ax_ch,   telem, "channel_util","Channel Utilisation","%")
    _plot(ax_air,  telem, "air_util_tx", "Air Utilisation TX", "%")
    _plot(ax_rssi, telem, "rssi",        "RSSI",               "dBm")
    _plot(ax_snr,  telem, "snr",         "SNR",                "dB")

    for ax in axes.flat:
        _fmt_xaxis(ax, tz_label, show_label=(ax in axes[-1]))

    out = os.path.join(data_dir, f"{label}_plot.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved → {out}")

# ── Gateway figure ────────────────────────────────────────────────────────────

def plot_gateway(data_dir: str, tz_label: str):
    gw = _load_csvs(data_dir, "gateway_position_*.csv", "ts")
    if gw.empty:
        print("[SKIP] No gateway position data found")
        return

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    ax_lat, ax_lon, ax_alt, ax_traj = axes.flat

    fig.suptitle(
        f"Gateway – Position Log\n{_session_str(gw['time'])}",
        fontsize=13, fontweight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.95], h_pad=3.5, w_pad=3.0)

    kw = dict(color=_GATEWAY_COLOUR, linewidth=1.5, marker=".", markersize=4)

    ax_lat.set_title("Latitude",  fontsize=11); ax_lat.set_ylabel("°", fontsize=9)
    ax_lat.grid(True, linestyle="--", alpha=0.4)
    ax_lat.plot(gw["time"], gw["latitude"], **kw)
    _fmt_xaxis(ax_lat, tz_label, show_label=False)

    ax_lon.set_title("Longitude", fontsize=11); ax_lon.set_ylabel("°", fontsize=9)
    ax_lon.grid(True, linestyle="--", alpha=0.4)
    ax_lon.plot(gw["time"], gw["longitude"], **kw)
    _fmt_xaxis(ax_lon, tz_label, show_label=False)

    ax_alt.set_title("Altitude",  fontsize=11); ax_alt.set_ylabel("m", fontsize=9)
    ax_alt.grid(True, linestyle="--", alpha=0.4)
    ax_alt.plot(gw["time"], gw["altitude"], **kw)
    _fmt_xaxis(ax_alt, tz_label, show_label=True)

    # Trajectory: longitude vs latitude, colour encodes time progression
    ax_traj.set_title("Trajectory", fontsize=11)
    ax_traj.set_xlabel("Longitude (°)", fontsize=9)
    ax_traj.set_ylabel("Latitude (°)",  fontsize=9)
    ax_traj.grid(True, linestyle="--", alpha=0.4)

    t_span = (gw["time"].max() - gw["time"].min()).total_seconds() + 1
    t_norm = (gw["time"] - gw["time"].min()).dt.total_seconds() / t_span

    sc = ax_traj.scatter(
        gw["longitude"], gw["latitude"],
        c=t_norm, cmap="plasma", s=18, zorder=3,
    )
    ax_traj.plot(gw["longitude"].iloc[0],  gw["latitude"].iloc[0],
                 "^", color="green", markersize=9, label="Start", zorder=4)
    ax_traj.plot(gw["longitude"].iloc[-1], gw["latitude"].iloc[-1],
                 "s", color="red",   markersize=9, label="End",   zorder=4)
    ax_traj.legend(fontsize=8)
    plt.colorbar(sc, ax=ax_traj, label="Time →")

    out = os.path.join(data_dir, "gateway_plot.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Saved → {out}")

# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(data_dir: str, nodes: list):
    print("\n── Session summary ──────────────────────────────────────────────────")
    for label in nodes:
        telem = _load_csvs(data_dir, f"{label}_telemetry_*.csv", "received_at")
        env   = _load_csvs(data_dir, f"{label}_env_*.csv",       "received_at")
        rssi_str = ""
        if not telem.empty and "rssi" in telem.columns:
            r = telem["rssi"].dropna()
            if not r.empty:
                rssi_str = f"RSSI: {r.min():.0f} – {r.max():.0f} dBm  |  "
        print(
            f"  {label}  |  {len(telem)} telemetry, {len(env)} env packets  |  "
            f"{rssi_str}"
            f"packets spanning {_session_str(telem['time']) if not telem.empty else 'N/A'}"
        )
    print()

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Plot LoRa TestBed session data")
    parser.add_argument(
        "--data-dir", default="data",
        help="Directory containing CSV files (default: data)",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    tz_label = _tz_label()
    nodes    = _discover_nodes(data_dir)

    if not nodes:
        print(f"No telemetry CSV files found in {data_dir!r}")
        return

    print_summary(data_dir, nodes)

    for i, label in enumerate(nodes):
        plot_node(label, data_dir, _PALETTE[i % len(_PALETTE)], tz_label)

    plot_gateway(data_dir, tz_label)

    plt.show()


if __name__ == "__main__":
    main()
