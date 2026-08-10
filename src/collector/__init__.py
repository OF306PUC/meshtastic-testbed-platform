"""
Proxy-site edge collector.

Two independent readers, one per device at a proxy site:

  node_logd   the LiLyGO's Meshtastic console  -> measurement point P2
  proxy_logd  the nRF52840's VCOM              -> measurement point P1

They are separate processes on purpose: the ports fail independently, and each
detects a node restart from its own log rather than coordinating.

Both publish to `meshtastic-testbed/<p1|p2>/proxy`, discriminated by a `kind`
field, and neither computes a delivery ratio. The TX-vs-RX join lives with the
broker and the database at the gateway; keeping the Pi a publisher keeps it
replaceable, which matters for a device in the field.
"""
