# Project Overview — Meshtastic TestBed Platform

> Living project document. Must never describe a state older than 2 sessions back.
> Owned by `doc-keeper`. Last updated: 2026-08-06 (session: MQTT→InfluxDB ingestion
> gap for the `message`/`pdr` topics, PBX frame format verified against the
> firmware contract, three-measurement schema).
>
> **Known gaps in this document** (flagged 2026-08-06): the containerised gateway
> receiver from the 2026-07-24 session is now covered by
> [`diagrams/container-topology.md`](diagrams/container-topology.md), but the
> ASCII pipeline diagram below and the directory tree still predate it
> (`src/gateway/Dockerfile`, `GATEWAY_SERIAL_PORT`). The
> `src/tools/field_testing/` subtree is missing from the tree entirely, and
> ADR-0001 is not referenced anywhere.

## Project identity

**Meshtastic TestBed Platform** — a research platform for LoRa mesh environmental
monitoring built on [Meshtastic](https://meshtastic.org/) firmware. It is designed to
host one or more **physical testbeds**; the first is the **San Joaquín** deployment
(`docs/testbeds/san-joaquin.md`). In that deployment, three solar-powered SensCAP sensor
nodes communicate over a LoRa mesh to a USB-connected LILYGO gateway. A Python-based data
pipeline (MQTT → InfluxDB via Telegraf) stores telemetry. An integrated Flask + SocketIO
dashboard at `monitor/` provides realtime visualisation and is part of this repo. All
node/gateway configuration is done through the Meshtastic Python CLI — no custom firmware
is written (that is Phase 2/3 work).

A second track studies **mesh scalability** (node count, hops, airtime, power) — see
`docs/scalability/README.md`; the radio hardware and per-node parameters under study are
catalogued via `docs/hardware/radio-inventory-schema.md`. The gateway host is also evolving
from a desktop to a **Raspberry Pi + 5G HAT** (`docs/architecture/gateway-rpi-5g.md`).

Operated at the **Cyber-Physical Systems Research and Technology Center,
Pontificia Universidad Católica de Chile**.

---

## Current status

Early / Experimental — all three phases of the roadmap are in flight:

- [x] LoRa mesh network established between 3 nodes and gateway
- [x] Python-based node and gateway configuration via Meshtastic CLI
- [x] Temperature & humidity telemetry flowing end-to-end
- [x] Per-node hop limit and device role configuration
- [x] Gateway configured as `CLIENT_MUTE` (receive-only, does not rebroadcast)
- [x] MQTT broker (Mosquitto), InfluxDB, and Telegraf containerised via Docker Compose
- [x] GPS data collection active
- [x] Codebase reorganised into `src/` package with shared `common/` layer
- [x] Channel PSK moved to gitignored `.env`; InfluxDB creds to gitignored `configuration.env`
- [x] Web dashboard integrated at `monitor/` and running via `docker compose up -d` (port 5000)
- [x] PBX message capture: `PRIVATE_APP` frames from the PBX parsed for
      `src_id`/`dst_id` and published as metadata on `.../message`
- [x] Cadence-based PDR tracking per `(node, flow)` from the known broadcast
      intervals in `mesh_config.json` (no sequence numbers needed)
- [x] PBX frame format verified against the firmware **source and a live
      capture**: two layouts keyed by portnum, ids are big-endian uint32 phone
      numbers published in decimal (the PBX repo's own docs say little-endian
      and are wrong — see memory `pbx-frame-wire-format`)
- [x] `message` and `pdr` topics actually ingested — Telegraf subscribed to
      neither between 2026-07-31 and 2026-08-06, so both were dropped
- [ ] `usbPower` / `isCharging` fields under debug (reported as 0 / false)
- [ ] SHT4X I2C reliability issue unresolved (see Known constraints)
- [ ] Message-level PDR needs a TX-side source to join against `pkt_id`
      (see Known constraints — the PBX's own log is unusable today)

---

## Vision

A fully instrumented, low-power outdoor LoRa mesh testbed capable of continuous environmental
monitoring (temperature, humidity, GPS, power) from solar nodes, with clean telemetry
ingest into InfluxDB and real-time visualisation via the integrated web dashboard (`monitor/`). Eventually
supports custom firmware to extend sensor coverage and fix known hardware-layer issues.

---

## Architecture & stack

### Hardware layer
- **3× Seeed Studio SensCAP Solar Node P1 Pro** — nRF52-based, solar-powered LoRa nodes.
  Sense temperature/humidity via SHT4X over I2C Grove, report GPS and power metrics.
- **1× LILYGO board** — ESP32-based, USB-connected to the host computer; acts as the
  LoRa mesh gateway (role: `CLIENT_MUTE`).
- **Host computer** — runs all Python scripts and Docker services.

### Firmware / radio layer
- Meshtastic firmware (v2.7.19) on all devices.
- Channel: `CPS_RTC`; region: `ANZ`; modem preset: `LONG_FAST`.
- Nodes use `LOCAL_ONLY` rebroadcast mode to isolate the mesh.
- PSK is a shared base64 key stored in `.env` (read at runtime by `src/common/radio_config.py`).

### Software / data pipeline
```
[LoRa nodes] --LoRa mesh--> [LILYGO gateway]
                                    |
                          src/gateway/receiver.py  (serial, --port required)
                                    |
                          src/gateway/mqtt_connector.py
                                    |
                          Mosquitto (MQTT broker, Docker)
                                    |
                          Telegraf (MQTT → InfluxDB bridge, Docker)
                                    |
                          InfluxDB (time-series DB, Docker)
                                    |
                          monitor/  (Flask + SocketIO dashboard, in-repo, port 5000)
```

MQTT topics under `meshtastic-testbed/<node-label>/`:

| Topic | Contents |
|---|---|
| `device` | device metrics (battery, voltage, channel/air utilisation, uptime) + PDR fields |
| `environment` | temperature, humidity + PDR fields |
| `position` | latitude, longitude, altitude + PDR fields |
| `message` | PBX frame metadata: `portnum`, `src_id`, `dst_id`, `fw_ver`, `pkt_id`, sizes, link quality. No PDR (messages have no cadence). Content is opt-in via `capture_content` |
| `pdr` | losses inferred while a flow was silent — the only way a fully dead node becomes visible |

> **Diagramas** (Mermaid, versionados, se renderizan en GitHub):
> [`diagrams/container-topology.md`](diagrams/container-topology.md) — los cinco
> servicios del compose, puertos, volúmenes y los dos caminos de datos del dashboard.
> [`diagrams/database-ingestion-schema.md`](diagrams/database-ingestion-schema.md)
> — camino completo del paquete a la measurement, más el esquema de tags/fields.
> [`diagrams/data-flow-measurement-points.md`](diagrams/data-flow-measurement-points.md)
> — los tres puntos de medición y qué pérdida atribuye cada tramo.

InfluxDB database: `cpsrtc_meshtastic_telemetry`, across **three measurements**:

| Measurement | Fed by | Tags |
|---|---|---|
| `telemetry` | `position`, `device`, `environment` | `node_id`, `node_label`, `topic` |
| `pbx_message` | `message` | `node_id`, `node_label`, `portnum`, `src_id`, `dst_id` |
| `pdr` | `pdr` | `node_label`, `flow`, `source` |

The split is not cosmetic. `message` carries `rssi`/`snr`/`hop` for the same
`node_id` as telemetry but at a phone-driven cadence, and `monitor/utils.py`
filters those charts by `node_id` alone — one shared measurement would silently
interleave two populations. `pkt_id` deliberately stays a *field*: it is unique
per packet and would grow the series cardinality without bound as a tag.

### PBX frame format

Two layouts, selected by portnum (contract:
`../meshpbx/docs/readings/client-integration.md` §4.1):

```
portnum=PRIVATE_APP (256)     [VERSION:1][SRC_ID:4][DST_ID:4][content:N]   routed unicast
portnum=TEXT_MESSAGE_APP (1)  [VERSION:1][SRC_ID:4][content:N]             broadcast, no dst
```

`SRC_ID`/`DST_ID` are 4-byte big-endian `uint32` phone identifiers (national
number without country code), published in decimal so they match the PBX's own
log and the phone's NODE_REG write — they are **not** Meshtastic node ids. The
parser validates the `VERSION` byte and reports anything other than `0x01` as
malformed, which is how the proposed v2 (a 2-byte `seq` at offset 9, not
implemented) will be caught instead of misread.

### PDR measurement

Packet delivery ratio is inferred from **inter-arrival gaps against the known
broadcast cadence** — no sequence numbers involved. Each `(node, flow)` pair
(flow ∈ device/environment/position) has its cadence declared in
`mesh_config.json`, the same source the provisioning scripts write to the radios:

```
missed = max(0, round(dt / T) - 1)        pdr = rx / (rx + missed)
```

Two properties worth knowing before reading the numbers:

- **`pdr` can never exceed 1.0.** The signal for a broken cadence assumption is
  `early_count` — a packet arriving inside one nominal interval, typically
  `position_broadcast_smart_enabled` left on.
- **A rolling window and a cumulative ratio are both reported.** The window
  holds `window_sec / T` slots, so a 1 h window over a 600 s position cadence is
  only 6 samples; `pdr_window_slots` exposes that thin denominator rather than
  hiding it.

Accuracy is ±1 packet per gap (the firmware defers broadcasts under channel
congestion, so T is nominal). Node reboots are detected from a falling
`uptimeSeconds` and their downtime gap is discarded instead of charged as radio
loss.

---

## Key design decisions

- **No firmware modification (Phase 1).** All configuration is applied via the Meshtastic
  Python CLI (`meshtastic --set`, `--ch-set`, etc.). This avoids a PlatformIO build chain
  but limits what can be customised (e.g., telemetry field completeness, sensor retry logic).

- **`CLIENT_MUTE` for the gateway.** The gateway receives all mesh traffic but does not
  rebroadcast. This prevents the gateway's USB-powered, non-solar device from amplifying
  traffic in a way that distorts hop counts and battery stats for the solar nodes.

- **`LOCAL_ONLY` rebroadcast on nodes.** Nodes only relay packets that originate on the
  local channel, preventing them from participating in foreign Meshtastic meshes on the
  same frequency. Critical for a controlled testbed environment.

- **`src/common/radio_config.py` as single source of truth.** Channel name, region, preset,
  and PSK are defined once and imported by both `src/gateway/configure.py` and
  `src/node/configure.py`. Previously duplicated — unification done in this session.

- **PSK and InfluxDB creds in gitignored env files.** `.env` holds the channel PSK (loaded
  by `radio_config.py`); `configuration.env` holds InfluxDB credentials (injected into
  Docker Compose via `env_file`). Both have `.example` templates committed to the repo.
  Note: PSK was present in git history before commit 63f101a — rotation recommended if
  the channel is used outside the lab.

- **`--port` is required for receiver.py.** Auto-detection was removed; the serial port must
  be specified explicitly (e.g., `--port /dev/ttyACM0`).

- **Web dashboard integrated into the monorepo (`monitor/`).** The earlier decision to keep
  the dashboard in a sibling repo (`../LoRa-TestBed-Web`) has been reversed. The Flask +
  SocketIO app now lives at `monitor/` and its `docker-compose` service has been merged into
  the root `docker-compose.yaml` — one `docker compose up` starts the full stack (mosquitto +
  influxdb + telegraf + web). The data contract between the pipeline and the dashboard
  (MQTT topics + InfluxDB schema) is tracked in memory `data-contract-gateway-web`.

---

## Directory structure

```
meshtastic-testbed-platform/
├── src/
│   ├── common/
│   │   ├── radio_config.py           # Channel/region/preset/PSK (reads .env)
│   │   └── meshtastic_cli.py         # Shared run()/retry helper
│   ├── gateway/
│   │   ├── configure.py              # Gateway configuration script
│   │   ├── configure_params.py       # Gateway parameter constants
│   │   ├── config.py                 # Gateway runtime config
│   │   ├── receiver.py               # Main receiver (--port required)
│   │   ├── mesh_receiver.py          # Mesh packet decoder
│   │   └── mqtt_connector.py         # MQTT publisher
│   ├── node/
│   │   ├── configure.py              # Node configuration script
│   │   └── configure_params.py       # Node parameter constants
│   └── tools/
│       ├── check_node_info.py        # Node inspector + radio/mesh health-check
│       └── plot_history.py           # InfluxDB telemetry plotter
│
├── firmware/
│   ├── erase_firmware/               # nRF52 erase UF2
│   └── upload_firmware/              # Meshtastic firmware UF2
│
├── docs/
│   ├── project-overview.md           # This file (living)
│   ├── session-log.md                # Session bitácora (living)
│   ├── testbeds/
│   │   └── san-joaquin.md            # San Joaquín deployment
│   ├── architecture/
│   │   └── gateway-rpi-5g.md         # RPi + 5G HAT gateway plan (exploring)
│   ├── scalability/
│   │   └── README.md                 # Scalability study framing (WIP)
│   ├── hardware/
│   │   └── radio-inventory-schema.md # Radio inventory spreadsheet schema
│   └── diagrams/                     # *.drawio.png, i2c_prob*.png, read_config.txt
│
├── monitor/                          # Realtime web dashboard (Flask + SocketIO)
│   ├── app.py                        # Flask application entry point
│   ├── utils.py                      # InfluxDB query helpers
│   ├── param.py                      # Dashboard configuration constants
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── templates/
│   │   ├── base.html
│   │   ├── map.html                  # Leaflet map (/map)
│   │   └── dashboard.html            # Live charts (/dashboard?node=<id>)
│   └── static/
│
├── mqtt/mosquitto.conf
├── telegraf/etc/telegraf.conf
├── docker-compose.yaml               # mosquitto + influxdb + telegraf + web
├── configuration.env                 # gitignored; copy from .example (holds DB_USERNAME/DB_PASSWORD)
├── configuration.env.example
├── .env                              # gitignored; copy from .example
├── .env.example
├── mesh_config.json                  # Per-node IDs, hop limits, roles
└── requirements.txt
```

---

## Known constraints

- **SHT4X I2C reliability (hardware).** A cable twist at the Grove connector causes
  marginal contact that passes the I2C address scan (ACK at `0x44`) but fails the
  multi-byte `readSerial()` call. The sensor is then dropped from the telemetry map with
  no runtime retry. Grove I2C Hub adds capacitance that compounds the issue.
  Workaround: relieve cable twist, reseat connectors, or bypass the hub.
  Fix: scoped for Phase 2 (firmware retry logic).

- **`usbPower`/`isCharging` fields.** Reported as `0` / `false` in current firmware build;
  root cause not yet diagnosed. Scoped for Phase 2 investigation.

- **PSK in git history.** The channel PSK was committed before the `.env` migration
  (visible in commit 63f101a and earlier). If the channel is used in a non-lab context,
  the key should be rotated.

- **`configuration.env` still tracked in git** (as of last session). Needs
  `git rm --cached configuration.env` before the next commit — flagged for git-lead.

- ~~**Node-label mismatch (cosmetic).**~~ **Resolved 2026-08-10, and it was not
  cosmetic.** `/api/nodes` did not query the database at all: it returned a
  hardcoded list whose labels were swapped relative to `mesh_config.json` — the
  file the gateway reads when it writes the `node_label` tag — so the API
  contradicted InfluxDB and anyone correlating a chart against the database by
  label was looking at the wrong node. The same list also carried coordinates
  ~2 km from anything the nodes had ever reported. It now calls
  `get_nodes_position()`, so the label has one source.

- **GPS latitude and longitude are frozen and identical across all three nodes.**
  Surfaced by the fix above, which had been masking it. Across 3,255 position
  reports the lat/lon is byte-identical for node-1, node-2 and node-3
  (−33.4757888, −70.5953792; min equals max on every node), while **altitude
  varies normally** per node (535–679 m). A `fixed_position` would freeze all
  three values, so the leading hypothesis is Meshtastic's imprecise-location
  feature: `position_precision` is a per-channel `module_settings` value that
  quantises lat/lon to a grid and leaves altitude untouched, and three nodes a
  few hundred metres apart would snap to one cell. The configure scripts set
  channel name and PSK but never touch it, so the channels carry whatever the
  firmware defaults to.
  **To verify:** `meshtastic --port <dev> --info` and read the channel's
  `moduleSettings.positionPrecision`. Anything below full precision is the cause.
  Until this is settled, treat position as unusable for anything spatial —
  including the adjacency matrix the deployment playbook wants to derive.

- **No license.** Project is currently unlicensed. License decision pending.

- **Message-level PDR has no TX-side source yet.** Telemetry PDR is inferred
  from silence against a known cadence; messages are aperiodic, so silence
  carries no information and the ratio needs ground truth for what was *sent*.
  The join key exists (`pkt_id`, recorded on RX), but the intended TX source —
  the PBX's own log over the nRF52840 VCOM — is unusable: `proxy_id_to_str()`
  in `../meshpbx` reads 16 bytes from a 4-byte array, so the
  src/dst it prints are out-of-bounds garbage. That is a firmware-side fix in a
  separate repo. Note also that InfluxQL cannot join two measurements on a field
  value, so the reconciliation must happen upstream (a subscriber to both
  streams) with the DB only storing the resulting metric.

- **Telegraf config is untested.** The 94 unit tests cover the publisher, not
  the ingestion path — which is why `message`/`pdr` were published to Mosquitto
  and dropped for six days without anything failing. An end-to-end test that
  publishes a synthetic payload and queries InfluxDB would close the loop.
