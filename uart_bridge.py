#!/usr/bin/env python3
"""
Use the NUC126's own UART as a bridge to whatever it is wired to (almost
certainly the MStar MST91A4Q1 scaler), driven over SWD from the Pi.

The stock firmware configures exactly one UART -- at 0x40154000, 38400 8N1 with
RX interrupts enabled -- while both I2C controllers sit unclocked. Halting the
Cortex-M0 stops the firmware servicing that UART, so we can drive the peripheral
directly: write UART_DAT to transmit, drain UART_DAT to receive.

    ./uart_bridge.py listen [seconds]     passively wait for traffic
    ./uart_bridge.py send <hex bytes>     transmit, then listen for a reply
    ./uart_bridge.py regs                 dump the UART register block
    ./uart_bridge.py release              reset and hand back to the firmware
"""

import sys
import time
sys.path.insert(0, "/home/strabo/optix")
from rgb import OpenOCD, ensure_daemon

UART = 0x40154000
DAT = UART + 0x00
INTEN = UART + 0x04
FIFO = UART + 0x08
LINE = UART + 0x0C
FIFOSTS = UART + 0x18
INTSTS = UART + 0x1C
BAUD = UART + 0x24
FUNCSEL = UART + 0x30

RXEMPTY = 1 << 14
TXFULL = 1 << 23

HIRC = 22118400


def baud_of(baud_reg, clkdiv0):
    """Decode UART_BAUD into an actual bit rate (HIRC source assumed)."""
    brd = baud_reg & 0xFFFF
    m1, m0 = (baud_reg >> 29) & 1, (baud_reg >> 28) & 1
    uart_clk = HIRC / (((clkdiv0 >> 8) & 0xF) + 1)
    if m1 and m0:
        return uart_clk / (brd + 2)
    if m1:
        return uart_clk / ((((baud_reg >> 24) & 0xF) + 1) * (brd + 2))
    return uart_clk / (16 * (brd + 2))


def drain(o, budget=64):
    """Pull every byte currently sitting in the RX FIFO."""
    out = bytearray()
    while len(out) < budget:
        if o.read32(FIFOSTS) & RXEMPTY:
            break
        out.append(o.read32(DAT) & 0xFF)
    return bytes(out)


def dump(tag, data):
    if not data:
        return
    hexs = " ".join(f"{b:02x}" for b in data)
    txt = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    print(f"  {tag} {len(data):3d}B  {hexs}\n{'':>10}|{txt}|")


def cmd_listen(o, seconds):
    print(f"listening {seconds}s on the scaler UART (core halted, "
          f"firmware not consuming bytes)...")
    end = time.time() + seconds
    total = bytearray()
    while time.time() < end:
        data = drain(o)
        if data:
            print(f"[{time.time() % 1000:7.2f}] RX", end="")
            dump("<-", data)
            total += data
        else:
            time.sleep(0.02)
    print(f"\ntotal received: {len(total)} bytes")
    return bytes(total)


def cmd_send(o, payload, wait=2.0):
    drain(o, 256)
    print("TX", end="")
    dump("->", payload)
    for b in payload:
        while o.read32(FIFOSTS) & TXFULL:
            pass
        o.write32(DAT, b)
    end = time.time() + wait
    got = bytearray()
    while time.time() < end:
        got += drain(o)
        time.sleep(0.02)
    if got:
        print("RX", end="")
        dump("<-", bytes(got))
    else:
        print(f"  <- (no reply within {wait}s)")
    return bytes(got)


def cmd_regs(o):
    clkdiv0 = o.read32(0x50000220)
    b = o.read32(BAUD)
    names = [("DAT", DAT), ("INTEN", INTEN), ("FIFO", FIFO), ("LINE", LINE),
             ("FIFOSTS", FIFOSTS), ("INTSTS", INTSTS), ("BAUD", BAUD),
             ("FUNCSEL", FUNCSEL)]
    for n, a in names:
        print(f"  {n:8} @{a:#010x} = {o.read32(a):#010x}")
    line = o.read32(LINE)
    bits = (line & 3) + 5
    stop = 2 if line & 4 else 1
    par = "none" if not (line & 8) else ("even" if line & 0x10 else "odd")
    print(f"\n  -> {baud_of(b, clkdiv0):.0f} baud, {bits}{par[0].upper()}{stop}, "
          f"FUNCSEL={o.read32(FUNCSEL)} (0=UART)")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    ensure_daemon()
    o = OpenOCD()

    if args[0] == "release":
        o.cmd("reset run")
        print("firmware reset and running")
        return

    o.cmd("halt")
    if args[0] == "listen":
        cmd_listen(o, float(args[1]) if len(args) > 1 else 15)
    elif args[0] == "send":
        cmd_send(o, bytes.fromhex("".join(args[1:]).replace(",", "")))
    elif args[0] == "regs":
        cmd_regs(o)
    else:
        sys.exit(__doc__)
    o.close()


if __name__ == "__main__":
    main()
