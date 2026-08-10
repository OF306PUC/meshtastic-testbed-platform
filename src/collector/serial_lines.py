"""
Line reader for a device console, with reconnection.

Shared by node_logd (the LiLyGO's Meshtastic console) and proxy_logd (the
nRF52840's VCOM). Both are read-only taps: nothing is ever written to the port.
"""

import time
import serial


class ConsoleReader:
    """
    Yields decoded lines from a serial console, reconnecting on failure.

    Two properties this class exists to guarantee:

    * **DTR/RTS are never asserted.** The standard ESP32 auto-reset circuit is
      wired to those lines, so merely opening the port reboots the board. A
      reader that reconnects after a hiccup would then reboot the proxy's node
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
        self._disable_hupcl()
        print(f"[SERIAL] open {self.port} @ {self.baudrate} (DTR/RTS low)")
        return ser

    def _disable_hupcl(self):
        """
        Clears HUPCL so the kernel does not drop DTR when the port is closed,
        which would reset the board on shutdown. Best-effort: not all platforms
        or adapters expose it, and failing to clear it is not fatal.
        """
        try:
            import termios
            fd = self._ser.fileno() if self._ser else None
            if fd is None:
                return
            attrs = termios.tcgetattr(fd)
            attrs[2] &= ~termios.HUPCL
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except Exception as exc:                     # pragma: no cover
            print(f"[SERIAL] could not clear HUPCL ({exc}); close may reset the board")

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

            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")
