# Scalability Study — Framing (WIP)

> **Status: EARLY STUDY.** This is a stub. The owner is still defining the
> governing equations, the architecture, and experimenting with the Meshtastic
> simulators. **Do not populate metrics or experiment tables yet** — this file
> only holds the framing and open questions so the study has a home to grow into.

## Goal

Characterise how the Meshtastic LoRa mesh testbed behaves as it **scales** — more
nodes, more hops, higher offered load — so we can predict limits before building
them physically. The radio hardware and per-node parameters under study are
tracked in `docs/hardware/radio-inventory-schema.md` (the spreadsheet schema).

## Metrics of interest (to be formalised)

Candidate metrics — equations and measurement method still **TBD by owner**:

- Packet Delivery Ratio (PDR) vs node count / hop count
- Average hop count and end-to-end latency
- Channel airtime utilisation and duty cycle (regulatory ceiling per region)
- Packet loss / collision rate under contention
- **Power consumption** per node (TX/RX/sleep duty) — of explicit interest;
  equations being defined by owner. Ties back to solar power budget.

## Method (planned)

- **Meshtastic simulators** — owner is exploring these to model mesh behaviour
  before/beyond the physical deployment. Capture which simulator, version, and
  configuration once settled.
- **Physical measurements** — from the San Joaquín testbed
  (`docs/testbeds/san-joaquin.md`) via the existing telemetry pipeline.
- **Analytical model** — equations relating airtime/preset/node-count to the
  metrics above (owner defining).

## Open questions

- Which Meshtastic simulator(s), and how do their assumptions map to `LONG_FAST`
  / `ANZ` / the real hardware?
- Governing equations for airtime, duty cycle, and power per node.
- Definition of "scale" for this study — node count? area? offered load? all?
- How power consumption is measured/modelled (bench vs firmware telemetry vs
  analytical).

_When the equations and experiment matrix are ready, add the Scalability
Experiments sheet to the radio inventory spreadsheet and document results here._
