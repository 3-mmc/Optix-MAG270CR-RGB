#!/usr/bin/env python3
"""
Drive the MSI Optix MAG270CR mainboard's Nuvoton NUC126NE4AE RGB controller
from a Raspberry Pi over bit-banged SWD.

The Nuvoton's PWM peripherals keep running while the Cortex-M0 core is halted,
so we park the stock firmware and write the compare registers ourselves.

Wiring (discovered by probing, see notes):
    Pi GPIO 25 -> ICE_CLK  (SWCLK)
    Pi GPIO 24 -> ICE_DAT  (SWDIO)
    Pi GPIO 18 -> nRESET   (left alone; pulled high on-board)
    Pi 3V3 / GND -> board VDD / GND

Usage:
    ./rgb.py probe          walk each channel one at a time (identify zones)
    ./rgb.py set <ch> <pct> set one channel 0-100
    ./rgb.py all <pct>      set every channel
    ./rgb.py off
    ./rgb.py sweep          slow fade across all channels
    ./rgb.py status         dump live PWM state
    ./rgb.py release        resume the stock firmware and let go
"""

import socket
import subprocess
import sys
import time
import os

OPENOCD_CFG = "/tmp/swd_daemon.cfg"
TCL_PORT = 6666

CFG = """
adapter driver linuxgpiod
adapter gpio swclk 25 -chip 0
adapter gpio swdio 24 -chip 0
transport select swd
adapter speed 100
swd newdap chip cpu -expected-id 0
dap create chip.dap -chain-position chip.cpu
target create chip.cpu cortex_m -dap chip.dap
bindto 127.0.0.1
tcl_port 6666
telnet_port 4444
gdb_port disabled
init
"""

# PWM compare registers. PWM0 ch0-5 and PWM1 ch3-5 are the nine enabled outputs.
CHANNELS = [(f"PWM0.{i}", 0x40040050 + 4 * i) for i in range(6)] + \
           [(f"PWM1.{i}", 0x40140050 + 4 * i) for i in (3, 4, 5)]

PERIOD = 0x6337          # 25399, as programmed by the stock firmware
POEN = {"PWM0": 0x400400D8, "PWM1": 0x401400D8}
CNTEN = {"PWM0": 0x40040020, "PWM1": 0x40140020}


class OpenOCD:
    """Minimal client for OpenOCD's TCL RPC (commands delimited by 0x1a)."""

    def __init__(self, host="127.0.0.1", port=TCL_PORT):
        self.sock = socket.create_connection((host, port), timeout=10)

    def cmd(self, command):
        self.sock.sendall(command.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf[:-1].decode(errors="replace").strip()

    def read32(self, addr):
        out = self.cmd(f"read_memory 0x{addr:08x} 32 1")
        return int(out.strip().split()[0], 0)

    def write32(self, addr, val):
        self.cmd(f"write_memory 0x{addr:08x} 32 {{{val}}}")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def ensure_daemon():
    """Start OpenOCD in the background if it isn't already listening."""
    try:
        socket.create_connection(("127.0.0.1", TCL_PORT), timeout=1).close()
        return
    except OSError:
        pass

    with open(OPENOCD_CFG, "w") as fh:
        fh.write(CFG)
    subprocess.Popen(
        ["openocd", "-f", OPENOCD_CFG],
        stdout=open("/tmp/openocd.log", "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(50):
        time.sleep(0.2)
        try:
            socket.create_connection(("127.0.0.1", TCL_PORT), timeout=1).close()
            return
        except OSError:
            continue
    sys.exit("openocd did not come up; see /tmp/openocd.log")


def connect(halt=True):
    ensure_daemon()
    oocd = OpenOCD()
    if halt:
        oocd.cmd("halt")
    return oocd


def duty(oocd, idx, pct):
    pct = max(0.0, min(100.0, float(pct)))
    # CMPDAT >= PERIOD parks the output permanently high, so clamp to PERIOD+1.
    val = int(round(PERIOD * pct / 100.0))
    if pct >= 100.0:
        val = PERIOD + 1
    name, addr = CHANNELS[idx]
    oocd.write32(addr, val)
    return name, val


def cmd_status(oocd):
    print(f"{'channel':10} {'CMPDAT':>8} {'duty':>7}   POEN/CNTEN")
    for unit in ("PWM0", "PWM1"):
        print(f"  {unit}: POEN=0x{oocd.read32(POEN[unit]):02x} "
              f"CNTEN=0x{oocd.read32(CNTEN[unit]):02x}")
    for i, (name, addr) in enumerate(CHANNELS):
        v = oocd.read32(addr)
        pct = 100.0 if v > PERIOD else 100.0 * v / PERIOD
        bar = "#" * int(pct / 5)
        print(f"[{i}] {name:8} {v:8} {pct:6.1f}%  {bar}")


def cmd_probe(oocd, dwell=1.5):
    print("Walking channels one at a time. Watch the board / LED headers.\n")
    for i in range(len(CHANNELS)):
        for j in range(len(CHANNELS)):
            duty(oocd, j, 100 if j == i else 0)
        name, _ = CHANNELS[i]
        print(f"  [{i}] {name:8} FULL ON  ... ", flush=True)
        time.sleep(dwell)
    for j in range(len(CHANNELS)):
        duty(oocd, j, 0)
    print("\ndone; all channels off")


def cmd_sweep(oocd, cycles=3):
    print("fading all channels (ctrl-c to stop)")
    try:
        for _ in range(cycles):
            for pct in list(range(0, 101, 4)) + list(range(100, -1, -4)):
                for j in range(len(CHANNELS)):
                    duty(oocd, j, pct)
                time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    for j in range(len(CHANNELS)):
        duty(oocd, j, 0)


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    action = args[0]

    if action == "release":
        # A plain resume leaves the stock animation loop stalled after a halt;
        # a reset restarts it cleanly.
        oocd = connect(halt=False)
        oocd.cmd("reset run")
        print("stock firmware reset and running; PWM handed back")
        return

    # Reads work fine on a running target, and halting for status would freeze
    # the stock animation we might be trying to observe.
    oocd = connect(halt=(action != "status"))
    if action == "status":
        cmd_status(oocd)
    elif action == "probe":
        cmd_probe(oocd)
    elif action == "sweep":
        cmd_sweep(oocd)
    elif action == "off":
        for j in range(len(CHANNELS)):
            duty(oocd, j, 0)
        print("all channels off")
    elif action == "all":
        for j in range(len(CHANNELS)):
            duty(oocd, j, args[1])
        print(f"all channels -> {args[1]}%")
    elif action == "set":
        name, val = duty(oocd, int(args[1]), args[2])
        print(f"{name} -> {args[2]}% (CMPDAT={val})")
    else:
        sys.exit(__doc__)
    oocd.close()


if __name__ == "__main__":
    main()
