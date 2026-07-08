# Project Overview — Meshtastic TestBed Platform

> Living project document. Must never describe a state older than 2 sessions back.
> Owned by `doc-keeper`. Last updated: 2026-07-08 (session: repo rename + reorg to
> `meshtastic-testbed-platform`, San Joaquín testbed docs, RPi+5G gateway plan,
> radio inventory schema).

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
- [ ] `usbPower` / `isCharging` fields under debug (reported as 0 / false)
- [ ] SHT4X I2C reliability issue unresolved (see Known constraints)

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
- Channel: `TB CPS-RTC`; region: `ANZ`; modem preset: `LONG_FAST`.
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

MQTT topics: `lora-testbed/<node-label>/device` and `lora-testbed/<node-label>/environment`.
InfluxDB database: `cpsrtc_lora_telemetry`, measurement: `mqtt_consumer`.

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
LoRa-TestBed-Platform/
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
│       ├── check_node_info.py        # Serial node info inspector
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

- **Node-label mismatch (cosmetic).** `monitor/app.py` maps `!7c70da02` → node-1 and
  `!0b64122b` → node-2, but `mesh_config.json` maps `!0b64122b` → node-1 and `!7c70da02` →
  node-2. Only affects the map popup label; data is unaffected. Owner should reconcile the
  two sources before the next release.

- **No license.** Project is currently unlicensed. License decision pending.
