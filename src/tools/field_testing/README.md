# Field-Testing Toolkit

Offline validation tools for **installing solar nodes in the field**. Separate
from the production pipeline (`src/gateway/` → MQTT → InfluxDB): everything here
is self-contained, logs to CSV, and needs no Docker.

There are two ways to confirm a freshly-installed node is healthy.

---

## Method 1 — BLE (Meshtastic app)

Quickest on-site check; no laptop needed. Devices have Bluetooth enabled
(`bluetooth.enabled: true`, see `example_config.yaml.example`).

1. Open the **Meshtastic** app (iOS/Android) and pair with the node over BLE,
   entering the device's 6-digit `fixedPin`.
2. Confirm the node's settings match the mesh: channel `telCPS_RTC`, region
   `ANZ`, preset `LONG_TURBO`, the expected role, and that GPS has a fix and
   telemetry (battery / temperature / humidity) reads sane values.

Use this for a fast go/no-go per node. For link-quality and coverage data over
time, use Method 2.

---

## Method 2 — Test receiver (CSV + plots)

Carry a portable gateway (a LILYGO on a laptop) and log everything it hears
while you walk or drive the coverage area.

### 1. Configure the portable gateway (once)

Joins the mesh's channels — telemetry (`telCPS_RTC`, idx 0) **and** messaging
(`msgPUC_NET`, idx 1) — from `src/common/radio_config.py`, and enables GPS so
its own track is logged. Needs `.env` at the repo root with both channel PSKs —
the same setup as production.

```bash
python src/tools/field_testing/configure_device.py --port /dev/ttyACM0
```

### 2. Run the receiver during install

Logs per-node telemetry / position (with RSSI/SNR) and the gateway's own GPS to
per-node CSV files. Known nodes are read from the repo-root `mesh_config.json`.

```bash
python src/tools/field_testing/receiver.py --port /dev/ttyACM0
# CSVs → <repo>/field-testing-data/   (override with --data-dir)
```

Leave it running while covering the area; stop with `Ctrl+C`.

### 3. Analyse the session

```bash
python src/tools/field_testing/plot_data.py                     # reads field-testing-data/
python src/tools/field_testing/plot_data.py --data-dir field-testing-data/run1
```

- **Per node:** temperature, humidity, channel / air utilisation, RSSI, SNR
  (gaps longer than 120 s are shaded as no-coverage).
- **Gateway:** latitude / longitude / altitude over time, plus a trajectory
  coloured by time (start ▲ green, end ■ red).

---

## Notes

- Requires the local virtualenv (`pip install -r requirements.txt` at the repo
  root — matplotlib, pandas, meshtastic). This is an interactive field tool and
  is intentionally **not** containerised.
- `field-testing-data/` (CSV output + generated `*_plot.png`) is gitignored.
- `example_config.yaml.example` is a sanitised reference export; the private
  key, channel URL and MQTT credentials are redacted. Copy it to
  `example_config.yaml` (gitignored) to keep a real local export.
- Radio settings come from the single source of truth,
  `src/common/radio_config.py`. Do not re-hardcode channel names or PSKs here.
