"""
Line reader for a device console, with reconnection.

Shared by node_logd (the LiLyGO's Meshtastic console) and pbx_logd (the
nRF52840's VCOM). Both are read-only taps: nothing is ever written to the port.
"""

import re
import time
import serial

# Both consoles colourise their output: Meshtastic wraps the level and parts of
# the message in SGR sequences, and Zephyr does the same on the nRF52840. They
# are invisible in a terminal and invisible again if the text is copied out of
# one, which is why the reference capture in docs/log-parsing.txt has none —
# reading the device directly is the only way to see them. Left in place they
# defeat every anchored pattern in the parsers, since the line no longer starts
# with the level. Stripping belongs here rather than in a parser: it is a
# property of reading a terminal, and both readers need it.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class ConsoleReader:
    """
    Yields decoded lines from a serial console, reconnecting on failure.

    Two properties this class exists to guarantee:

    * **DTR/RTS are never asserted.** The standard ESP32 auto-reset circuit is
      wired to those lines, so merely opening the port reboots the board. A
      reader that reconnects after a hiccup would then reboot the PBX's node
      on every reconnect — and every reboot re-anchors the gateway's cadence PDR
      estimator (CadencePdrTracker.reanchor), so the instrument would be
      corrupting the measurement it exists to take. `hupcl` is also cleared so
      closing the port does not reset it either.
    * **Garbage never propagates.** Serial lines arrive truncated or with
      partial UTF-8 after an overrun; decode errors are replaced rather than
      raised, and it is the parser's job to reject a line it cannot fully match.
    """

    def __init__(self, port: str, baudrate: int = 115200,
                 reconnect_delay: float = 5.0, read_timeout: float = 1.0):
        self.port            = port
        self.baudrate        = baudrate
        self.reconnect_delay = reconnect_delay
        self.read_timeout    = read_timeout
        self._ser            = None

    def _open(self):
        # port=None defers opening so dtr/rts can be set first; pyserial then
        # applies them as the port comes up instead of pulsing them.
        ser = serial.Serial()
        ser.port      = self.port
        ser.baudrate  = self.baudrate
        ser.timeout   = self.read_timeout
        ser.dtr       = False
        ser.rts       = False
        ser.exclusive = True      # a second reader would steal bytes, not copy them
        ser.open()
        self._disable_hupcl(ser)
        print(f"[SERIAL] open {self.port} @ {self.baudrate} (DTR/RTS low)")
        return ser

    @staticmethod
    def _disable_hupcl(ser):
        """
        Clears HUPCL so the kernel does not drop DTR when the port is closed.

        This is the setting that actually matters on a CH34x-based LiLyGO.
        Measured against real hardware: opening the port leaves the board alone
        (uptime kept climbing past three days), but CLOSING it with HUPCL set
        pulls DTR low, which the auto-reset circuit turns into a reboot. The
        board then comes up logging `reset_reason=reset` at uptime 0 — and every
        reboot re-anchors the gateway's cadence PDR estimator, so the collector
        would be corrupting the very measurement it exists to take.

        Takes the port explicitly: an earlier version read it off the instance,
        which is still unset while _open() is running, so the call silently did
        nothing and the board reset on every reconnect.
        """
        try:
            import termios
            attrs = termios.tcgetattr(ser.fileno())
            attrs[2] &= ~termios.HUPCL
            termios.tcsetattr(ser.fileno(), termios.TCSANOW, attrs)
        except Exception as exc:                     # pragma: no cover
            print(f"[SERIAL] WARNING: could not clear HUPCL ({exc}). "
                  f"Closing this port will reset the board.")

    def close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def iter_lines(self):
        """
        Yields one decoded line per console line, and **None** whenever the read
        times out with nothing pending.

        The None is not noise: it is the only tick the consumer gets while the
        device is silent, and expiry of in-flight packets has to happen then.
        A purely line-driven parser would hold a packet forever if the node went
        quiet right after transmitting it.
        """
        while True:
            if self._ser is None:
                try:
                    self._ser = self._open()
                except Exception as exc:
                    print(f"[SERIAL] open failed: {exc}; retrying in "
                          f"{self.reconnect_delay:.0f}s")
                    time.sleep(self.reconnect_delay)
                    continue
            try:
                raw = self._ser.readline()
            except Exception as exc:
                print(f"[SERIAL] read failed: {exc}; reopening")
                self.close()
                time.sleep(self.reconnect_delay)
                continue

            if not raw:
                yield None                      # idle tick
                continue

            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            yield _ANSI.sub("", text)
