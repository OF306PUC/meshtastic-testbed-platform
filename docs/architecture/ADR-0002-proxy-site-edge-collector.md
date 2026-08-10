# ADR-0002 — Raspberry Pi as the proxy-site edge collector

**Status:** Accepted — not implemented. Two preconditions open (see Open questions).
**Date:** 2026-08-06 · **Revised:** 2026-08-07 (P2 re-scoped, F3 cancelled, three Pis)
**Owner:** technical-director
**Related:** docs/diagrams/data-flow-measurement-points.md;
docs/diagrams/container-topology.md; docs/architecture/gateway-rpi-5g.md;
memory [[proxy-frame-wire-format]], [[data-contract-gateway-web]]

## Context

A proxy node is a pair: an **nRF52840** running the BLE proxy firmware
(`../meshtastic-ble-proxy`) joined over UART to a **LiLyGO** running stock
Meshtastic. Two such pairs exist, `p1` and `p2`, installed at **separate sites**.
Neither board can source power to the other, so each pair needs two USB
supplies — which, done from a host computer, pins every proxy to a desk.

That is the stated problem, and it is the least interesting one.

The real problem is **observability**. Today `p1`/`p2` are visible only through
what survives the LoRa hop to the gateway: every measurement of the proxy is
taken *through* the medium being measured. Three consequences:

- **Message-level PDR has no denominator.** Phone traffic is aperiodic, so the
  cadence estimator (`CadencePdrTracker`) does not apply — silence carries no
  information. Nothing records what was *sent*.
- **Three different losses are collapsed into one number.** A packet can die on
  BLE (phone→proxy), at the Stream API handoff over UART (proxy→node), or on the
  air (node→gateway). A low ratio does not say which.
- **Proxy-internal events are entirely invisible.** Phones connecting and
  disconnecting, the 6-connection limit being hit, TX-queue drops, UART overruns,
  node reboots seen from the proxy side.

The last one is the cheapest to fix and was overlooked: the firmware **already
logs all of it** over its USB VCOM, and nobody is reading that port.

## Decision

Install a **Raspberry Pi at each proxy site** as an **edge collector**: it powers
both boards, taps their two serial interfaces, and publishes metrics into the
existing MQTT→Telegraf→InfluxDB pipeline. It is a publisher, not a second
pipeline.

### What runs on it

1. **`proxy-logd`** — reads the nRF52840 VCOM, parses the firmware log, publishes
   counters and events to `meshtastic-testbed/<p1|p2>/proxy`, landing in a new
   `proxy_health` measurement via a fourth Telegraf block. **Unblocked; this is
   the primary job.** The line `TX queue full — ToRadio ... dropped` is a *direct*
   measurement of the proxy→node loss segment — a counter, not an inference.
2. **`node-logd`** — reads the LiLyGO's USB console as **plain text**, i.e.
   measurement point P2. Meshtastic emits a full TX lifecycle carrying the packet
   id, which is the same `pkt_id` the gateway already records:

   ```
   [Serial]  PACKET FROM PHONE  (id=0x19f326ba ... Portnum=256)   handed over UART1
   [Serial]  enqueue for send   (id=0x19f326ba ... encrypted len=36)
   [RadioIf] Started Tx         (id=0x19f326ba ...)
   [RadioIf] Packet TX: 260ms                                      airtime
   [RadioIf] Completed sending  (id=0x19f326ba ...)                went on air
   ```

   Three stages, so a packet the proxy handed over but the node never transmitted
   is distinguishable from one transmitted and lost on the air. The console also
   emits cumulative counters — `txGood=26,txRelay=16,rxGood=60,rxBad=0` — which
   are monotonic and therefore self-healing against dropped log lines.

   **This is a passive read, not a Stream API client** — see the rejected
   alternative below. It is what makes P2 possible at all.
3. **Local Mosquitto in bridge mode** — store-and-forward. Handles reconnection
   and on-disk queueing natively, so no spool code is written.
4. **NTP** — the TX↔RX reconciliation window requires the clocks to agree.

### What does not run on it

InfluxDB, Telegraf and the dashboard stay central. So does **the TX↔RX
reconciler**: TX is observed at the proxy but RX at the gateway, and the broker
and database live wherever the gateway runs. The reconciler becomes a new
service in the gateway's compose stack. Keeping the Pi a dumb publisher keeps it
replaceable — which matters for the device that sits in the field.

**Three Raspberry Pis in total**, not two: one collector per proxy site (`p1`,
`p2`, separate locations) plus the gateway. The gateway Pi **runs the full
compose stack** — Mosquitto, Telegraf, InfluxDB and the dashboard — and may
itself be housed at one of the proxy sites. Two consequences of that placement
are recorded under Open questions.

### Backhaul

**Building WiFi, 5 GHz (red MIDE).** No cellular, no HAT, no data plan. This
decouples the proxy sites from `gateway-rpi-5g.md`, which concerns moving the
*gateway* and remains open on its own terms.

### Power and grounding

The Pi powers both boards over USB. This is not only convenience: the nRF52840
and the LiLyGO are joined by UART and therefore **must** share a ground
reference. A single supply guarantees it.

## Consequences

**Positive**

- Three measurement points instead of one, so losses become attributable per
  segment rather than pooled.
- Proxy health becomes observable at all, with no firmware change required.
- Common ground guaranteed by construction.
- Nothing central changes except one Telegraf block and one new service; the
  data contract is extended additively.
- Each proxy site becomes self-contained and no longer tethered to a desk.

**Negative / costs**

- Three field devices, each with its own power, network and update burden.
- Parsers coupled to free-text log strings from two codebases we do not control
  — the proxy firmware and stock Meshtastic (see Open questions).
- Backhaul now spans a network we do not administer.
- **The gateway Pi must boot from a USB SSD, not a microSD.** It runs InfluxDB,
  and a time-series database writing continuously is the worst case for SD wear
  and for corruption on power loss. This is a requirement, not a preference.

**Precondition, not a follow-up: the broker must be authenticated.**
`mosquitto.conf` sets `allow_anonymous true` and `docker-compose.yaml` binds
`1883` on all interfaces. That was harmless while everything ran on one machine.
Publishing from the building WiFi turns it into an **open MQTT broker on a shared
network**, and for a measurement testbed the exposure is a *data-integrity*
problem before it is a security one: anyone on that network can inject telemetry
that is indistinguishable from real data after the fact, and `_is_valid()` cannot
help — it filters on a `node_id` carried *inside* the payload the writer controls.
Password auth plus per-publisher ACLs must land before the first Pi is installed.

## Alternatives considered (rejected)

- **A powered USB hub.** Solves the stated problem — two USB ports collapse to
  one — for a fraction of the cost, and solves nothing else. **Rejected because
  power was never the real problem:** without the Pi there are no P1/P2 taps and
  message-level PDR stays unmeasurable. Still the correct answer if a proxy pair
  ever lives permanently at a desk.
- **Two separate wall chargers.** Cheapest, and leaves the two grounds floating
  relative to each other across a UART link. Classic source of intermittent byte
  corruption that presents as firmware flakiness and costs days to diagnose.
  **Rejected.**
- **Reconciler on the Pi.** Would require the remote, least-reliable node to
  subscribe to the gateway's RX stream and hold correlation state across network
  drops. **Rejected** — the join belongs where both streams and the database
  already are.
- **Full pipeline on the Pi (edge InfluxDB).** Two more databases to reconcile
  later, for a central stack that already works. **Rejected**; revisit only if
  backhaul proves unreliable enough that store-and-forward is insufficient.
- **Waiting for the proposed frame `seq` (v2) to measure message PDR.**
  **Rejected:** `pkt_id` already exists on both sides and is a better key — it
  also carries timestamp and airtime, and needs no firmware change. See
  [[proxy-frame-wire-format]].
- **A Stream API client on the LiLyGO's USB for P2.** **Rejected — not possible:**
  the firmware permits only one Stream API instance, and the proxy already holds
  it over UART1. This killed the original P2 design. The passive console read
  replaces it and is strictly better: it needs no client, cannot contend with the
  proxy, and yields the TX lifecycle plus cumulative counters.
- **Asking the proxy firmware for a forward counter (`F3`).** **Cancelled:**
  stock Meshtastic already emits `txGood=…,txRelay=…,rxGood=…,rxBad=…` on the
  node console — cumulative, monotonic, self-healing against dropped log lines.
  The denominator needs no firmware change at all.
- **A 5G HAT per proxy.** **Rejected** — building WiFi covers it. Remains open
  for the gateway.

## Open questions

Blocking:

1. **Client isolation on the building WiFi.** Many managed networks block
   client-to-client traffic, which would leave the Pi with internet but no route
   to the gateway host on `1883`. Nothing we can configure — it must be verified
   and, if present, requested from whoever administers the network.
2. **A stable address for the broker.** DHCP moves the gateway host and mDNS
   `.local` is commonly blocked on managed networks. Needs a reservation or a
   static IP.

Non-blocking but shaping:

3. **Retransmissions change the arithmetic.** Phone traffic is sent with
   `WantAck=1`, so the node retransmits until acked (`Setting next retransmission
   in 5972 msecs` … `Received a ACK for 0x19f326ba, stopping retransmissions`).
   Telemetry is `WantAck=0`. Therefore: counting `Completed sending` events
   overcounts — **count unique ids**; and two distinct ratios exist, first-attempt
   delivery (link quality) and eventual delivery after retries (service quality).
   Message PDR and telemetry PDR are now non-comparable for a *second* reason,
   on top of one being an inference and the other a measurement. Note that the
   "implicit ack" is generated from hearing one's own packet rebroadcast — it
   confirms a hop, not arrival at the gateway.
4. **Log-format stability, now on two fronts.** The parsers depend on free-text
   strings from the proxy firmware *and* from stock Meshtastic. The proxy side we
   can ask to emit structured lines; the Meshtastic side is upstream and will
   drift across releases — the tree already carries both 2.7.19 and 2.7.26. Pin
   the parser to a firmware version and re-verify on every upgrade.
5. **P1's ids are garbage until fixed upstream.** `proxy_id_to_str()` reads 16
   bytes from a 4-byte array, so the `src`/`dst` the firmware prints are
   out-of-bounds reads. This does **not** block the health counters, nor the
   aggregate message PDR (which keys on `pkt_id` from the node console), but it
   does block **per-handset attribution**: the node console carries the packet id
   and never the app-level `src_id`.
6. **Opening the LiLyGO's USB can reset the node.** The standard DTR/RTS
   auto-reset circuit fires when a host opens the port. A reader that reconnects
   after a hiccup would reboot the proxy node — and every reboot triggers
   `CadencePdrTracker.reanchor()`, so the instrument would be corrupting the
   measurement. DTR assertion must be suppressed.
7. **Gateway placement biases what it measures.** If the gateway Pi is housed at
   a proxy site, that proxy's packets arrive at maximum RSSI with zero hops while
   the other's do not, so `p1` vs `p2` stops measuring the mesh and starts
   measuring gateway placement. Usable if chosen deliberately — the near node
   becomes a control with RF effectively removed, isolating proxy-chain losses —
   but it must be recorded, not discovered later. The prior question, whether the
   gateway still hears the solar nodes from there, cannot be answered from the
   docs: `docs/testbeds/san-joaquin.md` still has placement and distances as
   `_TBD (owner)_`.
8. ~~**`PRIVATE_APP` rides channel 0.**~~ **Resolved 2026-08-10** — fixed
   app-side; portnum 256 now goes out on channel 1 as intended. The capture in
   `docs/log-parsing.txt` predates the fix and still shows `Ch=0x0`, so tests
   against it pin the old value on purpose; the live check is
   `SELECT DISTINCT channel FROM proxy_message WHERE portnum='PRIVATE_APP'`.

## Follow-ups

- ~~Enable broker auth~~ **DONE 2026-08-07** (config only): `mqtt/mosquitto.conf`
  with `allow_anonymous false`, `mqtt/aclfile` with five per-role accounts
  (`p1`/`p2` confined to their own subtree), `mqtt/init-credentials.sh`, and
  credential plumbing through the gateway, monitor and Telegraf. **Still to run
  before the next `docker compose up`:** `./mqtt/init-credentials.sh` and paste
  the result into `configuration.env` — until then the broker rejects everyone.
- ~~Capture `channel`~~ **DONE 2026-08-07** — tag on `proxy_message`, field on
  `mqtt_consumer`; `SELECT DISTINCT channel FROM proxy_message` is the query that
  reports whether the phone-app fix has shipped.
- ~~`node-logd`~~ **DONE 2026-08-10** — `src/collector/{serial_lines,node_logd}.py`
  plus a fourth Telegraf block feeding a `proxy_health` measurement. Seven
  anchored patterns, a per-`pkt_id` lifecycle with five outcomes, and 18 tests
  run against `docs/log-parsing.txt` rather than invented lines. Verified end to
  end against a live broker and InfluxDB: the record reaches `proxy_health` with
  `pkt_id` as a field, and it is the same integer the gateway stores, so the join
  key lines up.
- **Still missing before a field deployment: the local Mosquitto bridge.**
  `compose.collector.yaml` publishes straight to the gateway's broker, which is
  right for bench work and wrong in the field — every WiFi outage silently eats
  the TX ground truth the collector exists to produce.
- **`proxy-logd` is now unblocked**: `proxy_id_to_str()` was fixed upstream
  (big-endian, decimal, no out-of-bounds read), so the proxy log can finally
  attribute a loss to a specific handset.
- Fourth Telegraf block → `proxy_health`; extend [[data-contract-gateway-web]].
- Reconciler service in the gateway compose, with a grace window before charging
  a `pkt_id` as lost, and de-duplication of retransmitted ids.
- Ask the proxy repo for: the `proxy_id_to_str()` fix, structured `EVT` lines,
  and a correction to `client-integration.md` §4.1/§4.2, which state
  little-endian where the code and the wire are big-endian.
- Pi hygiene: correctly sized PSU (Pi 4 → 3 A, Pi 5 → 27 W, or USB current is
  throttled and devices drop), `/dev/serial/by-id/` instead of `ttyACM*`,
  `wlan0` power save off, no log accumulation on SD, and USB-SSD boot on the
  gateway Pi.
