# Meshtastic TestBed Platform

> A LoRa mesh testbed for environmental monitoring **and** for measuring how well the mesh itself performs. Built on Meshtastic firmware with solar-powered nodes, a gateway, and instrumentation that records what the radios actually did. Designed to host one or more physical deployments — the first is **San Joaquín** (see [`docs/testbeds/san-joaquin.md`](docs/testbeds/san-joaquin.md)).

**Cyber-Physical Systems Research and Technology Center**
[Pontificia Universidad Católica de Chile](https://www.uc.cl)

---

## What this platform can do

**Collect environmental telemetry from remote solar nodes.** Temperature, humidity, battery voltage and charge level, and GPS position arrive over LoRa, land in a time-series database, and show up on a live map and dashboard. Nodes are configured entirely through the Meshtastic CLI — no firmware changes.

**Measure packet delivery, not just receive packets.** Every node broadcasts on a known cadence, so silence is evidence. The platform estimates a per-flow delivery ratio and, crucially, records losses *while a node is quiet* — which is the only way a completely dead node becomes visible rather than simply absent.

**Carry text messages between phones over the mesh, and measure that too.** Two PBX sites bridge phones onto the mesh over Bluetooth. Their frames are recorded separately from sensor telemetry, with the phone identities, link quality and packet id preserved, so message delivery can be measured end to end.

**See what the radios did, not only what arrived.** Optional edge collectors read the consoles of the PBX hardware and record transmit outcomes — sent, acknowledged, unacknowledged, dropped, lost to a reboot — plus per-handset delivery counters and reboot events. This is the difference between "the packet never showed up" and "the packet was never sent".

**Verify a deployment instead of trusting it.** A single inspector command reads a radio, health-checks it against the repo's declared configuration, cross-checks the mesh it can hear against the planned node list, and exits non-zero on any drift. It also flags coordinates that arrived coarsened by a precision setting — a fault that is invisible in the vendor tooling.

**Validate node placement in the field, offline.** A portable receiver logs telemetry with RSSI/SNR plus the operator's own GPS track to CSV, then plots the session — no Docker, no database, no network.

**Analyse history offline.** Export any field across all nodes to CSV, or plot several days of any measurement, straight from the database.

---

## Hardware

| Component | Count | Role |
|---|---|---|
| **Seeed SensCAP Solar Node P1 Pro** | ×3 | Solar sensor nodes — temperature, humidity, power, GPS |
| **LILYGO board** | ×1 | Gateway radio, `CLIENT_MUTE` — hears the mesh, never rebroadcasts |
| **Raspberry Pi** | ×1 | Gateway host — permanent, runs the whole pipeline. Backhaul is the building's WiFi; a 5G HAT for cellular backhaul is a later option, not a dependency (see [`docs/architecture/gateway-rpi-5g.md`](docs/architecture/gateway-rpi-5g.md)) |
| **PBX site** (nRF52840 + LILYGO over UART) | ×2 | Bridges phones onto the mesh over BLE. Sites `p1` and `p2` |

The two PBX sites and the three sensor nodes share one mesh. Sensor telemetry and phone messages travel on **separate channels**, so they can be measured independently.

<table><tr>
<td align="center" width="33%">
  <img src="docs/diagrams/sensecap-node.drawio.png" width="100%" alt="SensCAP Node Architecture"/>
  <br/><em>SensCAP sensor node</em>
</td>
<td align="center" width="33%">
  <img src="docs/diagrams/gateway.drawio.png" width="100%" alt="Gateway LILYGO Architecture"/>
  <br/><em>LILYGO gateway</em>
</td>
<td align="center" width="33%">
  <img src="docs/diagrams/mesh.png" width="100%" alt="Mesh Network Topology"/>
  <br/><em>Mesh network topology</em>
</td>
</tr></table>

---

## Running it

Work through these in order. Everything after the hardware steps is one-time-per-machine; the runtime itself is a single command.

### Clone the repo and create the config files

```bash
git clone https://github.com/OF306PUC/meshtastic-testbed-platform.git
cd meshtastic-testbed-platform

cp .env.example .env                            # channel PSKs, gateway serial port
cp configuration.env.example configuration.env  # database + broker credentials
```

Fill both in. Neither is in git. `.env` holds the two channel pre-shared keys — every radio in the mesh must carry the same ones or it hears nothing at all.

### Generate the broker credentials

The MQTT broker refuses anonymous clients, so this step is **mandatory**.

```bash
./mqtt/init-credentials.sh     # creates mqtt/pwfile, prints an MQTT_* block
```

Paste the printed block into `configuration.env`, replacing the `change_me` placeholders. The password file and `configuration.env` must be generated **together on each host** — copying one and regenerating the other leaves the hashes out of step with the passwords, and the result looks like nothing being wrong at all: containers up, dashboard rendering, zero data.

Five accounts are created, one per role. The two PBX collectors may write only their own subtree, so neither site can forge the other's measurements.

### Install the Python tools — only if you are provisioning or plotting

The runtime is entirely containerised. Nothing here is needed to *run* the platform, and none of it belongs on the gateway Pi.

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt            # provisioning + inspection
pip install -r requirements-analysis.txt   # only if you also plot
```

The two sets are split on purpose: the analysis half pulls matplotlib, pandas and numpy, which compile from source wherever a platform ships no wheel. Configuring a radio should not cost a numeric stack.

> **If `pip` fails with TLS or certificate errors on a Raspberry Pi, check the clock before the packages** — `timedatectl`. A Pi has no battery-backed clock and restores a stale time at boot, which makes valid certificates look not-yet-valid and breaks `pip`, `apt` and `docker pull` alike.

### Flash Meshtastic firmware onto each sensor node

Required before any configuration — the CLI cannot talk to a device without Meshtastic firmware. Flashing the SensCAP nodes is drag-and-drop; no tools needed.

Connect the device, double-press reset to enter bootloader mode, and it appears as a USB drive. Drop `firmware/erase_firmware/nrf_erase_sd7_3.uf2` onto it first if the device is new or reused. Double-press reset again, then drop the newest firmware from `firmware/upload_firmware/` on. The device reboots on its own.

The LILYGO boards are ESP32 and use a different method — see the [Meshtastic flashing docs](https://meshtastic.org/docs/getting-started/flashing-firmware/). Full nRF52 reference: [UF2 drag-and-drop guide](https://meshtastic.org/docs/getting-started/flashing-firmware/nrf52/drag-n-drop/).

### Register each node in `mesh_config.json`

Find a radio's hardware ID with the inspector, then add an entry under `nodes_cfg` with its role, hop limit, broadcast intervals and — once installed — its surveyed position.

```bash
python src/tools/check_node_info.py --port /dev/ttyACM0
```

Surveyed positions are a **record read by the monitor**, never written to the radio. Keeping them separate is what lets the difference between the surveyed point and the GPS report be read as GPS error.

### Configure every device

Bring the stack **down first** if it is running — the gateway container holds the serial port and will fight you for it.

```bash
python src/gateway/configure.py            --port /dev/ttyUSB0
python src/node/configure.py    --node-id 1 --port /dev/ttyUSB0   # repeat for 2, 3
python src/pbx/configure.py     --node-id p1 --port /dev/ttyUSB0  # repeat for p2
```

Each script reads the shared radio settings, applies both channels, and reboots the device. Run them for **every** device including the gateway: several settings are applied by the *sender*, so one unconfigured radio degrades its own data no matter how the rest of the mesh is set.

### Verify before you trust

```bash
python src/tools/check_node_info.py --port /dev/ttyACM0
```

Exits non-zero on any mismatch, so it doubles as a smoke test. It reports the radio's own configuration, compares region, preset, rebroadcast mode, both channels and both PSKs against the repo, lists every node it can hear against the planned list, and flags any node still transmitting coarsened coordinates — naming which one to reconfigure.

### Bring the stack up

With the gateway plugged in:

```bash
docker compose up -d --build
docker compose ps
```

> **Always pass `--build`.** The gateway and dashboard images bake their Python in with `COPY`, so a plain `up -d` silently restarts the old code after a `git pull` or a local edit.

Point the gateway at a stable device path, not `ttyACM0` — that numbering is assignment order, not identity, and it changes between reboots. With the wrong path the container starts cleanly and hears nothing.

```bash
ls -l /dev/serial/by-id/          # pick the LILYGO / CH34x entry
GATEWAY_SERIAL_PORT=/dev/serial/by-id/usb-... docker compose up -d --build
```

The host user needs to be in the `dialout` group to open the device (`sudo usermod -aG dialout "$USER"`, then log out and back in).

| Service | Role |
|---|---|
| `mosquitto` | MQTT broker |
| `influxdb` | Time-series database, persisted across restarts |
| `telegraf` | Bridges MQTT into the database |
| `gateway-receiver` | Reads the mesh from the USB gateway and publishes it |
| `web` | Live dashboard — [http://localhost:5000](http://localhost:5000) |

### Open the dashboard

| Route | Shows |
|---|---|
| `/map` | All nodes on a map — surveyed positions where recorded, GPS otherwise |
| `/dashboard?node=<id>` | Live charts for one node: temperature, humidity, power |

### Confirm data is actually flowing

```bash
docker exec -it influxdb influx
```

```sql
USE cpsrtc_meshtastic_telemetry
SHOW MEASUREMENTS
SELECT * FROM telemetry ORDER BY time DESC LIMIT 5
```

Four measurements should be listed:

| Measurement | Holds |
|---|---|
| `telemetry` | Sensor telemetry and position, plus the delivery-ratio fields that ride with it |
| `pbx_message` | Phone-message frames: identities, channel, packet id, link quality |
| `pdr` | Losses inferred while a flow was silent — how a dead node stays visible |
| `pbx_health` | What the PBX hardware itself reported: transmit outcomes, per-handset counters, reboots |

`pbx_health` appears only once the edge collectors are running. The other three should be there as soon as the mesh is alive.

### Optionally, run the edge collectors at a PBX site

These are instrumentation, not part of the runtime — start them when you want to know what the PBX hardware did, and stop them when you do not.

```bash
cd src/pbx/collector
cp .env.example .env      # site, the two serial ports, gateway address
docker compose up -d --build
docker compose logs -f
```

Both boards' consoles are read as text and published to the same broker. Point the two ports at the wrong boards and **both containers come up clean and report nothing** — the consoles share no grammar, so every line is silently rejected. Watch the rejected-line counter on the first dump.

---

## Analysing what you collected

```bash
# Plot several days of any field, across every node at once
python src/tools/plot_history.py --days 4 --fields rssi snr

# Export raw history to CSV for offline work
python src/tools/plot_history.py --days 7 --export history.csv
```

Plots break at real gaps rather than interpolating across an outage, and values that are placeholders rather than measurements are dropped by default.

For validating a node's placement while installing it, the field-testing toolkit logs a session to CSV with RSSI/SNR and the operator's GPS track, then plots it — see [`src/tools/field_testing/README.md`](src/tools/field_testing/README.md).

```bash
python src/tools/field_testing/configure_device.py --port /dev/ttyACM0
python src/tools/field_testing/receiver.py         --port /dev/ttyACM0
python src/tools/field_testing/plot_data.py
```

---

## Configuration

Three files decide how the mesh behaves. Everything else derives from them.

| File | Decides | In git |
|---|---|---|
| `src/common/radio_config.py` | Mesh-wide radio settings: both channels, region, modem preset, rebroadcast mode, position precision | Yes |
| `mesh_config.json` | Per-node identity, role, hop limit, broadcast cadences, surveyed position | Yes |
| `.env` | The two channel pre-shared keys | **No** |

Radio settings are declared **once**. Every provisioning script imports them, so the gateway and the nodes cannot drift apart — and the inspector checks a live radio against the same file.

Broadcast cadences live only in `mesh_config.json`, because the delivery-ratio estimator measures against them: a cadence written in two places is a measurement that silently lies.

Secrets stay out of version control in `.env`, `configuration.env` and `mqtt/pwfile`. The broker's ACL file sits beside the password file but is **not** secret — it is topic permissions, tracked on purpose.

---

## Repository layout

```
meshtastic-testbed-platform/
│
├── src/
│   ├── common/          # Shared radio settings, mesh config loader, CLI helpers
│   ├── gateway/         # Gateway provisioning + the receiver service (containerised)
│   ├── node/            # Sensor-node provisioning
│   ├── pbx/             # PBX-attached node provisioning
│   │   └── collector/   # Edge collectors: transmit outcomes + PBX health (containerised)
│   └── tools/
│       ├── check_node_info.py   # Inspector / health check
│       ├── plot_history.py      # History plots + CSV export
│       └── field_testing/       # Offline field-install validation
│
├── monitor/             # Live dashboard (containerised)
├── telegraf/            # MQTT → database bridge config
├── mqtt/                # Broker config, ACL, credential generator
├── firmware/            # Erase + Meshtastic UF2 binaries for the nRF52 nodes
├── tests/               # Unit tests for the parsers, trackers and config loaders
├── docs/                # Architecture decisions, diagrams, deployment playbook, session log
│
├── docker-compose.yaml  # The runtime stack
├── mesh_config.json     # Per-node mesh parameters
├── requirements.txt              # Provisioning + inspection dependencies
└── requirements-analysis.txt     # Plotting dependencies (optional)
```

---

## When something is wrong

The failure mode worth learning to recognise: **containers up, dashboard rendering, zero data.** Almost always credentials, and quiet by design.

```bash
docker logs telegraf | grep -i connect               # expect a Connected per input
docker logs meshtastic-testbed-web | grep '\[MQTT\]' # expect connected, not rc=5
docker logs meshtastic-testbed-mqtt-broker | grep -i "not authoris"
```

`rc=5` or `not authorised` means the password in `configuration.env` does not match the hash in `mqtt/pwfile`. Regenerate both together — or, if the code was just updated, rebuild: an image carrying pre-credential code connects anonymously and is refused.

To watch the broker directly, which works even when the database does not:

```bash
docker exec meshtastic-testbed-mqtt-broker mosquitto_sub \
  -t 'meshtastic-testbed/#' -v -u monitor -P "$MQTT_PASSWORD_MONITOR"
```

**Nodes all reporting the same coordinates** means their position precision is reduced — the radio masks off the low bits before transmitting, so the detail is gone before it reaches the gateway. `check_node_info.py` names the offending nodes. Reconfiguring fixes new data; already-stored positions cannot be recovered.

**A sensor detected but no telemetry** is usually the I2C cable, not the code. The bus scan and the driver init are separate steps, and a marginal Grove connection passes the scan and fails the init — see [`docs/architecture/ADR-0001-canopy-sensor-i2c-link.md`](docs/architecture/ADR-0001-canopy-sensor-i2c-link.md).

---

## Status

Working today:

- LoRa mesh across three sensor nodes, two PBX sites and the gateway
- Separate telemetry and messaging channels, provisioned from one declared source
- Temperature, humidity, power and GPS telemetry into InfluxDB, live on the dashboard
- Cadence-based delivery-ratio estimation, including losses during silence
- Phone-message frames recorded with identities and link quality
- Authenticated broker with per-role topic permissions
- Edge collectors for transmit outcomes and PBX health
- Inspector that health-checks a radio and the mesh against the repo
- Field-install validation toolkit and offline history export

In progress:

- Reconfiguring the deployed radios after the position-precision fix
- Joining transmit records to receive records offline, for end-to-end message delivery

Next:

- Custom sensor integration and low-power tuning in firmware
- Building firmware from source for both node and gateway boards

Architecture decisions and their reasoning live in [`docs/architecture/`](docs/architecture/); the deployment procedure is in [`docs/deployment-playbook.md`](docs/deployment-playbook.md).

---

## References

- [Meshtastic Documentation](https://meshtastic.org/docs/) · [Python API](https://python.meshtastic.org/)
- [UF2 Flashing Guide](https://meshtastic.org/docs/getting-started/flashing-firmware/nrf52/drag-n-drop/)
- [Seeed SensCAP Solar Node P1 Pro](https://www.seeedstudio.com/) · [LILYGO LoRa Boards](https://www.lilygo.cc/)

---

## License

Currently unlicensed. To be determined.

---

*Developed at the Cyber-Physical Systems Research and Technology Center, Pontificia Universidad Católica de Chile.*
