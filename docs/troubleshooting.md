# Troubleshooting

Organised by **symptom**, because that is what you have when you arrive here.

Almost everything in this catalogue is a *quiet* failure: the containers stay up,
the dashboard renders, the tool exits zero, and nothing is obviously broken. That
is not bad luck — a mesh testbed has many places where the honest outcome is "no
data", and "no data" looks identical whether the cause is a wrong password, a
wrong serial port, or a genuinely silent radio. Each entry below therefore says
how to tell the difference, not just what to change.

| Symptom | Section |
|---|---|
| Containers up, dashboard renders, no data at all | [Nothing arrives and nothing errors](#nothing-arrives-and-nothing-errors) |
| Sensor detected at `0x44`, no temperature or humidity | [I2C sensor detected but no telemetry](#i2c-sensor-detected-but-no-telemetry-sht4x) |
| `pip`, `apt` or `docker pull` fail on certificates | [A Raspberry Pi with a stale clock](#a-raspberry-pi-with-a-stale-clock) |
| Every node reports the same coordinates | [Positions all identical](#positions-all-identical) |
| Gateway container healthy, hears nothing | [The gateway is on the wrong serial port](#the-gateway-is-on-the-wrong-serial-port) |
| Both collectors up, neither reports anything | [The collector ports are crossed](#the-collector-ports-are-crossed) |
| A code change has no effect | [Compose restarted the old image](#compose-restarted-the-old-image) |
| `unknown shorthand flag: 'd' in -d` | [Compose v2 is not installed](#compose-v2-is-not-installed) |
| Data written, dashboard shows nothing recent | [Measurements landing in the past](#measurements-landing-in-the-past) |
| Serial port busy, or the board reboots when you connect | [Serial port contention](#serial-port-contention) |

---

## Nothing arrives and nothing errors

**Symptom.** `docker compose ps` shows every service `Up`, the dashboard loads,
and there is no data anywhere.

Almost always credentials, and quiet by design: the broker refuses anonymous
clients, the refused clients keep retrying, and nothing crashes.

```bash
docker logs telegraf | grep -i connect               # expect one Connected per input
docker logs meshtastic-testbed-web | grep '\[MQTT\]' # expect connected, not rc=5
docker logs meshtastic-testbed-mqtt-broker | grep -i "not authoris"
```

`rc=5` or `not authorised` means the password in `configuration.env` does not
match the hash in `mqtt/pwfile`. Those two must be generated **together on each
host** — copying one and regenerating the other leaves them out of step.

If the code was just updated, rebuild before re-checking: an image still carrying
pre-credential code connects anonymously and is refused. See
[Compose restarted the old image](#compose-restarted-the-old-image).

To separate "the broker never got it" from "the database never stored it", watch
the broker directly. This works even when the database side is broken:

```bash
docker exec meshtastic-testbed-mqtt-broker mosquitto_sub \
  -t 'meshtastic-testbed/#' -v -u monitor -P "$MQTT_PASSWORD_MONITOR"
```

Traffic here but empty measurements means Telegraf is the problem, not the
gateway.

---

## I2C sensor detected but no telemetry (SHT4X)

**Symptom.** The node reports detecting an SHT4X at address `0x44`, but no
temperature or humidity ever appears in MQTT or InfluxDB.

### Physical setup

<table><tr>
<td align="center" width="30%">
  <img src="diagrams/i2c_prob1.png" width="60%" alt="Grove port with cable twist highlighted"/>
  <br/><em>Grove port on the node board. Note the cable twist at the connector.</em>
</td>
<td align="center" width="30%">
  <img src="diagrams/i2c_prob2.png" width="40%" alt="Grove I2C Hub inside the enclosure"/>
  <br/><em>Grove I2C Hub inside the enclosure.</em>
</td>
</tr></table>

### Why detection is not enough

The bus scan and the driver init are **separate steps**. The scan only checks for
an ACK at `0x44`. Init then reads the sensor's serial number via `readSerial()`,
and if that read fails the sensor is dropped from `nodeTelemetrySensorsMap`
permanently — there is no retry at runtime.

A cable twist at the Grove connector makes contact marginal enough to pass the
short ACK and fail the multi-byte serial read. The Grove I2C Hub adds capacitance
that degrades signal integrity further.

> **`SHT4X found at address 0x44` is not a reliable indicator that telemetry will
> flow.** Read the two lines after it instead.

### Telling failure from success in the serial monitor

| After `Init sensor: SHT4X` | Failure | Success |
|---|---|---|
| Next line | `Error trying to execute readSerial()` | `serialNumber : 11d75c14` |
| Result | `Can't connect to detected SHT4X sensor` | `Opened SHT4X sensor on i2c bus` |

### What to try

- **Relieve the cable twist** — the Grove cable should sit flat and unstressed at
  the connector.
- **Reseat every Grove connector** at both the node board and the hub, then
  power-cycle.
- **Connect the SHT4X directly** to the Grove port, no hub, to rule out
  hub-induced capacitance.
- **Read the boot log** for `serialNumber :` rather than trusting the scan line.

> `Could not open / read /prefs/uiconfig.proto` and the same for
> `cannedConf.proto` are normal and unrelated.

The capacitance budget behind this, and the differential-I2C fix that resolves it
properly, are in
[`architecture/ADR-0001-canopy-sensor-i2c-link.md`](architecture/ADR-0001-canopy-sensor-i2c-link.md).

---

## A Raspberry Pi with a stale clock

**Symptom.** `pip`, `apt` or `docker pull` fail with TLS or certificate errors —
"certificate has expired or is not yet valid", "Release file is not valid yet".
Packages appear to fail at random.

A Pi has no battery-backed clock. At boot it restores the last time saved to disk
and only then syncs over the network, so a Pi powered off for a fortnight comes up
a fortnight behind. Valid certificates then look **not yet valid**, and every
TLS client breaks at once — which reads as many unrelated failures rather than one
cause.

```bash
timedatectl                              # compare against the real date
sudo timedatectl set-ntp true
sudo systemctl restart systemd-timesyncd
timedatectl                              # want: System clock synchronized: yes
```

A managed building network may block NTP (UDP 123), in which case it will never
sync on its own. Either point the Pi at a reachable time server — the gateway host
works — or set the clock by hand to unblock:

```bash
sudo date -s "YYYY-MM-DD HH:MM:SS"
```

Fix this **before** starting containers, not after: see
[Measurements landing in the past](#measurements-landing-in-the-past) for why.

---

## Positions all identical

**Symptom.** Every node reports the same latitude and longitude, or nodes
hundreds of metres apart plot on the same point.

Their channel `position_precision` is reduced. Coordinates travel as int32 in
units of 1e-7 degrees, and the setting keeps only the top bits — so the detail is
**masked off by the sender before transmission**. Precision 14, for example,
snaps everything to a ~2.9 km grid.

This is invisible in `meshtastic --info`: protobuf omits default values when
serialising, so while the field holds the firmware default it simply does not
appear. Absence reads as "unset" and cannot be distinguished from "set to 14" by
inspection. The inspector checks it directly instead, and names the nodes to
reconfigure:

```bash
python src/tools/check_node_info.py --port /dev/ttyACM0
```

The provisioning scripts set precision 32 on both channels. Run them on **every**
device including the gateway — the mask is applied by the sender, so one
unconfigured radio coarsens its own positions regardless of the rest of the mesh.

> **Already-stored positions cannot be recovered.** The low bits were discarded
> before transmission. Spatial analysis restarts from the reconfiguration, which
> is what the surveyed positions in `mesh_config.json` are for — they give the
> real geometry independently of what the radios report.

---

## The gateway is on the wrong serial port

**Symptom.** `gateway-receiver` is `Up`, its log shows no errors, and no mesh
traffic ever arrives.

`ttyACM*` numbering is assignment order, not identity: it changes between reboots
and depends on what else is plugged in. On this testbed `ttyACM0` has been a
sensor node while the gateway sat on `ttyACM1`. Pointed at the wrong device the
container opens it happily and hears nothing.

```bash
ls -l /dev/serial/by-id/          # pick the LILYGO / CH34x entry
GATEWAY_SERIAL_PORT=/dev/serial/by-id/usb-... docker compose up -d --build
```

Always use a `by-id` path. If the container cannot open the device at all, the
host user is probably not in `dialout`:

```bash
sudo usermod -aG dialout "$USER"    # then log out and back in
```

---

## The collector ports are crossed

**Symptom.** Both `node-logd` and `pbx-logd` are `Up`, neither errors, and
`pbx_health` stays empty.

The two consoles share no grammar, so a collector pointed at the wrong board
rejects every line it reads — silently, because rejecting an unrecognised line is
normal behaviour, not an error.

Watch the rejected-line counter on the first dump (~30 s). If it climbs in step
with lines seen, the ports are swapped:

```bash
cd src/pbx/collector
docker compose logs -f
```

The LILYGO node console enumerates as `usb-1a86_*`, the nRF52840's J-Link VCOM as
`usb-SEGGER_J-Link*`. Set them in `.env` beside the compose file, by `by-id` path.

---

## Compose restarted the old image

**Symptom.** A code change, or a `git pull`, has no effect at all.

`gateway-receiver`, `web` and the collectors bake their Python into the image with
`COPY`. Plain `docker compose up -d` sees a running container and leaves it alone
— it does **not** rebuild.

```bash
docker compose up -d --build
```

Telegraf is immune because it mounts its config as a volume, which is why a
Telegraf change can appear to work while an application change does not.

---

## Compose v2 is not installed

**Symptom.** `docker compose up -d --build` prints `unknown shorthand flag: 'd'
in -d` and a usage block.

The `-d` reached a command that does not accept it, which means `compose` is not
being resolved as a Docker subcommand — usually a host with the old standalone
`docker-compose` (v1) and no v2 plugin.

```bash
docker compose version      # want: Docker Compose version v2.x
docker-compose --version    # if only this works, you have v1
```

```bash
sudo apt-get update && sudo apt-get install -y docker-compose-plugin
```

**v1 is not a fallback.** `src/pbx/collector/compose.yaml` uses the top-level
`name:` key, which exists only in the Compose Spec — v1 fails to validate the
file. On a Pi where Docker came from `apt install docker.io`, installing from the
official Docker repository gets a matching CLI, daemon and plugin.

If `apt` itself fails on certificates, fix the clock first: see
[A Raspberry Pi with a stale clock](#a-raspberry-pi-with-a-stale-clock).

---

## Measurements landing in the past

**Symptom.** Data is being published and stored, but the dashboard and any
"last N hours" query show nothing.

The gateway receiver and both collectors stamp each record with the **local wall
clock**, and Telegraf uses that stamp as the database timestamp. A host running
twelve days behind writes every point twelve days into the past — silently, since
nothing about a wrong timestamp is invalid.

This is worse than a failed install, because it corrupts the record instead of
stopping. In InfluxDB the timestamp is part of a point's identity, so misfiled
points cannot be corrected in place; they have to be rewritten or discarded.

Check the clock on **every** host that publishes — the gateway Pi and each
collector Pi — before starting containers:

```bash
timedatectl
```

See [A Raspberry Pi with a stale clock](#a-raspberry-pi-with-a-stale-clock) for
the fix.

---

## Serial port contention

**Symptom.** A provisioning script fails to open the port, or a board reboots the
moment something connects to it.

**Only one process can hold a serial port.** The containers hold theirs for as
long as they run, with `restart: unless-stopped` reopening them after a crash. So
bring the stack down before provisioning:

```bash
docker compose down                    # releases the gateway port
cd src/pbx/collector && docker compose down   # releases both collector ports
```

An editor or serial monitor left open on the port counts too.

**Opening a LILYGO's USB console resets the board.** The ESP32 auto-reset circuit
is driven by the serial control lines, so connecting is itself a reboot. The
collector lowers those lines and clears HUPCL, but the reset on open still
happens. It is harmless for a one-off read; for a permanent install, wire the
console over GPIO UART instead of USB.

---

## Still stuck?

The chronological record of what broke and how it was diagnosed is in
[`session-log.md`](session-log.md). Architecture decisions and their reasoning are
in [`architecture/`](architecture/). The step-by-step deployment procedure is in
[`deployment-playbook.md`](deployment-playbook.md).
