# Interfacing with the MStar MST91A4Q1 scaler through the Nuvoton

Follow-on investigation: can the NUC126 be used as a bridge to the board's main
MStar scaler? **Yes — the link exists, it is identified, and the bridge works.
But nothing answers, almost certainly because the scaler is unpowered.**

---

## Method: find the link by clock gating

An unclocked Nuvoton peripheral reads back all-zero, so a plain sweep of the
peripheral space only shows blocks the firmware actually uses. Forcing every
clock on (`AHBCLK`/`APBCLK0`/`APBCLK1` := `0xFFFFFFFF`) makes every *implemented*
peripheral appear at its reset values. Diffing the two sweeps separates
"exists" from "in use" — and `reset run` restores the firmware's own gating.

`scan_all.py` does this. Result: **20 peripheral blocks implemented, 13 used.**

Writeback of `0xFFFFFFFF` also reveals which clock bits are implemented at all:

| Register | Firmware value | All-on readback |
|---|---|---|
| `AHBCLK`  | `0x003f8016` | `0x003f809e` |
| `APBCLK0` | `0x0834001d` | `0x5837337f` |
| `APBCLK1` | `0x0000003c` | `0x0000003c` (only 4 bits implemented, all already on) |

## What the firmware uses

Base addresses confirmed against the NUC126 datasheet memory map:

| Base | Peripheral | In use | Notes |
|---|---|---|---|
| `0x40154000` | **UART2** | **yes** | **the scaler link** — 38400 8N1 |
| `0x40050000` | UART0 | no | unclocked, reset values |
| `0x40150000` | UART1 | no | unclocked, reset values |
| `0x40020000` | I2C0 | **no** | implemented but never clocked |
| `0x40120000` | I2C1 | **no** | implemented but never clocked |
| `0x40030000` | SPI0 | no | unclocked |
| `0x40034000` | SPI1 | no | unclocked |
| `0x40060000` | **USBD** | **yes** | USB 2.0 FS device controller — see below |
| `0x40040000` / `0x40140000` | PWM0 / PWM1 | yes | the nine RGB channels |
| `0x40004000` | WDT | yes | |
| `0x40008000` | RTC | yes | reads a plausible BCD time |
| `0x40010000` / `0x40110000` | TMR01 / TMR23 | yes | |
| `0x5000c000` | FMC | yes | see "flash settings area" below |
| `0x50008000` | PDMA | yes | |
| `0x50014000` | HDIV | yes | hardware divider |
| `0x50018000` | CRC | no | |

**Both I2C controllers are implemented but never clocked.** The Nuvoton does not
talk to the scaler over I2C. There is exactly one external serial link.

---

## The scaler link: UART2

| | |
|---|---|
| Peripheral | UART2 @ `0x40154000` (`UART2_BA`) |
| Pins | **PB.1 = UART2_TXD** (MFP3), **PB.4 = UART2_RXD** (MFP9) |
| Baud | **38400** |
| Framing | **8N1** (`LINE = 0x03`) |
| Mode | `FUNCSEL = 0` (normal UART) |
| Interrupt | IRQ12 = `UART02_INT`; handler relocated to SRAM at `0x20003c31` |

### Deriving the baud rate

```
BAUD    = 0x300000be  -> bits[29:28] = 0b11 = Mode 2, BRD = 190
CLKSEL1 -> UARTSEL = 3 = HIRC = 22.1184 MHz
CLKDIV0 -> UARTDIV = 2 -> /3 -> UART_CLK = 7.3728 MHz

Mode 2:  baud = UART_CLK / (BRD + 2) = 7372800 / 192 = 38400   (exact)
```

An exact standard baud rate falling out of that chain is a strong check on the
whole decode. IRQ12 is shared (`UART0 and UART2`), and since UART0 is unclocked,
that SRAM-resident handler belongs to UART2.

---

## Results

### Passive listen: no traffic

Halting the core stops the firmware consuming received bytes, so the RX FIFO can
be drained directly over SWD. **25 seconds of listening produced zero bytes**, with
no framing, parity or break errors flagged in `FIFOSTS`.

### Transmit: works

Writes to `UART_DAT` at 38400 showed no FIFO accumulation — but that is expected
rather than a failure, since each SWD write costs ~1 ms while a byte at 38400 baud
takes only 0.26 ms, so the FIFO drains as fast as it can be filled.

Re-testing with the baud rate temporarily dropped to ~300 makes it unambiguous:

```
idle:                 FIFOSTS = 0xb0404000   TXPTR=0  TXEMPTY=1
after 8 byte writes:  FIFOSTS = 0xa0074000   TXPTR=7  TXEMPTY=0
after drain:          FIFOSTS = 0xb0404000   TXPTR=0  TXEMPTY=1
```

**The transmit path is fully functional.** The bridge works; nothing replies.

### Why nothing replies

The board is powered *solely* from the Pi's 3.3 V rail into the Nuvoton. The MStar
MST91A4Q1 needs its own core rails from the board's DC-DC converters, which require
the monitor's 12 V input. **The scaler is almost certainly unpowered**, so there is
nothing on the far end of the UART to answer.

PB.4 (RXD) does read idle-high, but that is equally consistent with a simple pull-up
resistor as with a powered transmitter, so it does not settle the question on its own.

---

## Second external interface: USB

`0x40060000` is `USBD_BA`, the USB 2.0 full-speed **device** controller, and the
firmware has it clocked with a non-zero configuration (`0x0000011f`), while most of
the block reads zero — consistent with a device that is configured but has never
enumerated, i.e. no host attached.

This fits the MSI Mystic Light architecture: the monitor presents a USB device that
MSI's software drives to set the lighting. **If the board's USB upstream is wired to
the Pi, the Nuvoton may enumerate and expose the stock RGB control interface — no SWD
required.** That is the single highest-value thing to try next, and it needs only a
cable.

(Individual USBD register offsets above are inferred; the base address is from the
datasheet memory map. The datasheet does not carry the USBD register table — that is
in the separate Technical Reference Manual.)

---

## Incidental finding: flash settings area

FMC (`0x5000c000`) was caught mid-operation with `ISPADR = 0x0001f85c` and a related
address of `0x0001f800` — the last page of the 128 KB APROM. The firmware's used
extent ends at `0x1f860`. So the tail of flash is an emulated-EEPROM settings area,
almost certainly where the RGB mode/brightness is persisted, rather than code.

---

## Next steps

1. **Power the board properly** from the monitor's 12 V supply, then re-run
   `./uart_bridge.py listen`. If the scaler is alive it should talk on boot.
2. **Try USB.** Connect the board's USB upstream to the Pi and check `dmesg` /
   `lsusb` for an enumeration.
3. **Recover the protocol from firmware.** The UART2 ISR at `0x20003c31` (present in
   `dumps/sram.bin`) parses the frame format. Disassembling it would give the sync
   byte and framing without needing a live scaler.

## Tooling

```
./uart_bridge.py regs           decode the UART2 register block
./uart_bridge.py listen [secs]  drain RX directly (core halted)
./uart_bridge.py send <hex>     transmit, then wait for a reply
./uart_bridge.py release        reset and hand back to the firmware
./scan_all.py                   force all clocks on and map every peripheral
```
