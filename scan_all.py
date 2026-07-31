#!/usr/bin/env python3
"""
Reveal the NUC126's full peripheral map by force-enabling every peripheral clock.

An unclocked Nuvoton peripheral reads back as all-zero, so the default scan only
shows blocks the stock firmware uses. Turning every clock on makes every
*implemented* peripheral appear at its reset values; diffing the two scans
separates "exists" from "in use".

Clock gating is non-destructive and `reset run` restores the firmware's own setup.
"""

import sys
sys.path.insert(0, "/home/strabo/optix")
from rgb import OpenOCD, ensure_daemon

AHBCLK, APBCLK0, APBCLK1 = 0x50000204, 0x50000208, 0x5000020C
PAGE = 0x4000          # peripherals decode on 16 KB boundaries (verified by aliasing)
WORDS = 8

RANGES = [
    ("APB1", 0x40000000, 0x40100000),
    ("APB2", 0x40100000, 0x40200000),
    ("AHB",  0x50000000, 0x50030000),
]


def sweep(o):
    found = {}
    for label, start, end in RANGES:
        for base in range(start, end, PAGE):
            try:
                out = o.cmd(f"read_memory 0x{base:08x} 32 {WORDS}")
                vals = [int(x, 0) for x in out.split()]
            except Exception:
                continue
            if vals and any(vals):
                found[base] = (label, vals)
    return found


def main():
    ensure_daemon()
    o = OpenOCD()
    o.cmd("halt")

    before_clk = {n: o.read32(a) for n, a in
                  (("AHBCLK", AHBCLK), ("APBCLK0", APBCLK0), ("APBCLK1", APBCLK1))}
    print("firmware clock gating: " +
          "  ".join(f"{n}=0x{v:08x}" for n, v in before_clk.items()))

    in_use = sweep(o)

    for a in (AHBCLK, APBCLK0, APBCLK1):
        o.write32(a, 0xFFFFFFFF)
    print("all peripheral clocks forced on: " +
          "  ".join(f"0x{o.read32(a):08x}" for a in (AHBCLK, APBCLK0, APBCLK1)))

    all_blocks = sweep(o)

    print(f"\n{'base':>12}  {'bus':<5} {'used?':<7} first 8 words")
    for base in sorted(all_blocks):
        label, vals = all_blocks[base]
        mark = "IN USE" if base in in_use else "-"
        print(f"  {base:#010x}  {label:<5} {mark:<7} " +
              " ".join(f"{v:08x}" for v in vals))

    newly = sorted(set(all_blocks) - set(in_use))
    print(f"\n{len(in_use)} blocks used by firmware, "
          f"{len(all_blocks)} implemented, {len(newly)} newly revealed:")
    print("  " + " ".join(f"{b:#010x}" for b in newly))

    o.cmd("reset run")
    print("\nfirmware reset and running; clock gating restored by reset")
    o.close()


if __name__ == "__main__":
    main()
