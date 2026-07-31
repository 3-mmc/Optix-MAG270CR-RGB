#!/usr/bin/env python3
"""
Sweep the NUC126's peripheral address space and report which blocks are alive.

Unclocked / unimplemented peripherals either fault on the bus or read back as
all-zero, so a page with non-zero content is a peripheral the stock firmware is
actually using. This sidesteps needing the exact TRM memory map.
"""

import sys
sys.path.insert(0, "/home/strabo/optix")
from rgb import OpenOCD, ensure_daemon

RANGES = [
    ("APB1", 0x40000000, 0x40100000),
    ("APB2", 0x40100000, 0x40200000),
    ("AHB",  0x50000000, 0x50030000),
]
PAGE = 0x1000
WORDS = 8


def main():
    ensure_daemon()
    o = OpenOCD()
    print(f"{'address':>12}  {'first 8 words':<0}")
    for label, start, end in RANGES:
        print(f"\n===== {label} {start:#010x}-{end:#010x} =====")
        hits = 0
        for base in range(start, end, PAGE):
            try:
                out = o.cmd(f"read_memory 0x{base:08x} 32 {WORDS}")
                vals = [int(x, 0) for x in out.split()]
            except Exception:
                continue
            if not vals or not any(vals):
                continue
            hits += 1
            print(f"  {base:#010x}  " + " ".join(f"{v:08x}" for v in vals))
        if not hits:
            print("  (nothing live)")
    o.close()


if __name__ == "__main__":
    main()
