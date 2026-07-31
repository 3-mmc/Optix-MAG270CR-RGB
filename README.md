# Reviving the RGB controller on a dead MSI Optix MAG270CR mainboard

Notes and tooling from bringing up the **Nuvoton NUC126NE4AE** backlight/Mystic Light
MCU on a salvaged MSI Optix MAG270CR mainboard (**HKCMNT MST91A4Q1-J Rev:03**),
driven over bit-banged SWD from a Raspberry Pi 4.

The panel and T-CON are gone, so the MStar MST91A4Q1 scaler has nothing to do —
but the secondary Nuvoton MCU that drives the RGB backlight is fully alive, its
flash is unlocked, and it can be taken over completely without modifying a single
byte of its firmware.

---

## Result

Full real-time control of **nine PWM channels — three RGB zones**, from the Pi,
with the stock firmware parked and restorable at any time.

```
$ ./rgb.py status
  PWM0: POEN=0x3f CNTEN=0x3f
  PWM1: POEN=0x38 CNTEN=0x38
[0] PWM0.0      25400  100.0%  ####################
[1] PWM0.1          0    0.0%
[2] PWM0.2          0    0.0%
...
```

---

## Hardware / wiring

The Nuvoton's ICP header was wired to the Pi's GPIO. Only three GPIOs had anything
attached — found by flipping each pin's internal pull-up/pull-down and seeing which
ones an external circuit overrode:

| Pi pin | Nuvoton | Notes |
|---|---|---|
| GPIO 25 | ICE_CLK (SWCLK) | |
| GPIO 24 | ICE_DAT (SWDIO) | |
| GPIO 18 | nRESET | on-board pull-up; left undriven |
| 3V3 header pin | VDD | board runs entirely off the Pi's 3.3 V |
| GND header pin | GND | |

All three signal pins read **high against the Pi's internal pull-down**, which both
identified them and proved the target was powered. The SWCLK/SWDIO assignment was
then recovered by brute-forcing all six orderings and watching for a valid DPIDR.

### A note on determining this safely

Driving a pin that is actually a power rail would short it through the Pi's pad.
This was ruled out without any risky probing: the board is powered *only* from the
Pi, yet every GPIO was configured as an **input**, so no GPIO could be sourcing that
power. VDD therefore had to be on the Pi's dedicated 3V3 header pin, leaving all
three GPIOs as signals.

---

## Chip identification

| | |
|---|---|
| DPIDR | `0x0bb11477` (SW-DP, Cortex-M0) |
| Core | Cortex-M0 r0p0, 4 breakpoints / 2 watchpoints |
| PDID | `0x00c05206` |
| APROM | **128 KB** — reads fault at `0x00020000` |
| SRAM | 20 KB @ `0x20000000` |
| SWD read throughput | ~29 KiB/s (libgpiod bit-bang) |

**Flash is not locked** — the whole APROM reads out cleanly.

OpenOCD 0.12's `numicro` flash driver does *not* recognise PDID `0x00c05206`
("Failed to detect a known part"), so erase/program through that driver is
unavailable. Plain memory-mapped reads work regardless, which is all a backup needs
— and as it turns out, no flash writing is required for full LED control.

### Firmware observations

- Occupies 129120 of 131072 bytes — nearly the entire flash. This MCU does far more
  than LEDs (OSD, buttons, power sequencing).
- No ASCII strings anywhere.
- Relocates its interrupt handlers into SRAM and executes from there — the halted
  PC sits at `0x200042b4`. Vector entries for IRQ4/5/6/9/10/12 point at `0x2000xxxx`;
  unused vectors all point at a common `0x0000011b` stub.
- Reads at `0x00100000` simply **alias APROM**, so the dump named `ldrom.bin` is an
  aliasing artefact, not the real LDROM. The genuine LDROM was never captured.
- CONFIG at `0x00300000` is not plain memory-mapped; it requires an FMC ISP command.

---

## PWM / LED mapping

Nine PWM outputs are enabled, matching exactly the nine pins muxed to alternate
function 6 (`PA0–PA2`, `PC0–PC4`, `PD7`):

| Zone | Channels | Pins | Connector |
|---|---|---|---|
| A | PWM0 ch0, ch1, ch2 | PC.0, PC.1, PC.2 | white JST #1 |
| B | PWM0 ch3, ch4, ch5 | PC.3, PC.4, PD.7 | white JST #2 |
| C | PWM1 ch3, ch4, ch5 | PA.2, PA.1, PA.0 | white JST #3 |

The grouping was confirmed by the *live* compare registers under the stock firmware:

| Zone | CMPDAT |
|---|---|
| A | 400, 0, 25500 |
| B | 3000, 0, 25500 |
| C | 5700, 0, 25500 |

Three identical triplets with the first channel phase-shifted 400 → 3000 → 5700:
one wave animation staggered across three RGB zones. The board has exactly three
white JST connectors.

### Register map (verified by observation)

| | |
|---|---|
| PWM0 base | `0x40040000` |
| PWM1 base | `0x40140000` |
| `PERIOD[ch]` | `base + 0x30 + 4*ch` = `0x6337` (25399) |
| `CMPDAT[ch]` | `base + 0x50 + 4*ch` |
| `CNT[ch]` | `base + 0x90 + 4*ch` |
| `CNTEN` | `base + 0x20` — PWM0 `0x3f`, PWM1 `0x38` |
| `POLCTL` | `base + 0xD4` — `0x00` (normal polarity) |
| **`POEN`** | **`base + 0xD8`** — PWM0 `0x3f`, PWM1 `0x38` |

`WGCTL0 = 0xAAA` / `WGCTL1 = 0x555` → active-high edge-aligned PWM (output high at
zero point, low at compare point). So **duty = CMPDAT / PERIOD**, and
`CMPDAT > PERIOD` parks the output permanently on.

> **Gotcha:** `0xD4` is POLCTL and `0xD8` is POEN. Reading `0xD4` and calling it POEN
> yields `0x00` and the false conclusion that all outputs are disabled.

### What is still unknown

Which channel within each triplet is **red, green and blue**. That needs LEDs
physically attached to a JST header; `./rgb.py probe` walks one channel at a time
for visual identification.

---

## Full pin map

Every pin the firmware muxes away from GPIO, resolved against the NUC126 datasheet
MFP tables:

| Pin | MFP | Function |
|---|---|---|
| PC.0 – PC.4 | 6 | PWM0_CH0 – PWM0_CH4 |
| PD.7 | 6 | PWM0_CH5 |
| PA.2, PA.1, PA.0 | 6 | PWM1_CH3, PWM1_CH4, PWM1_CH5 |
| **PB.1** | 3 | **UART2_TXD** — link to the MStar scaler |
| **PB.4** | 9 | **UART2_RXD** |
| PE.6 / PE.7 | 1 | ICE_CLK / ICE_DAT — the SWD pins used here |
| PF.3 / PF.4 | 1 | XT1_OUT / XT1_IN — external crystal |

Nine PWM pins, matching `POEN = 0x3f` / `0x38` exactly. That PE.6/PE.7 resolve to
the ICE pins we are physically driving is a useful cross-check on the whole decode.

---

## Talking to the MStar scaler

See **[SCALER.md](SCALER.md)**. Summary: the Nuvoton's only external serial link is
**UART2 at 38400 8N1 on PB.1/PB.4** — both I2C controllers are implemented but never
clocked. `uart_bridge.py` can transmit and receive through it (transmit verified by
watching the FIFO back up at reduced baud), but nothing replies, almost certainly
because the scaler is unpowered on the Pi's 3.3 V alone.

The firmware also has the **USB device controller** (`0x40060000`) clocked and
configured but un-enumerated — likely the Mystic Light host interface, and the
cheapest thing to try next.

---

## The takeover technique

Two properties make this work without touching flash:

1. **The PWM peripheral keeps running while the Cortex-M0 core is halted.** The
   counters continue to advance with the core stopped.
2. **A halted core stops rewriting CMPDAT**, so values written over SWD stay put.

So: halt the core, write the compare registers over SWD, and the hardware keeps
generating the waveforms. Verified stable across repeated writes and readbacks.

**Restoring the stock firmware requires `reset run`, not `resume`.** Halting stalls
the firmware's animation loop; a plain resume leaves the LEDs frozen, while a reset
brings the animation straight back.

---

## Usage

```
./rgb.py status          # live duty on all 9 channels (does NOT halt the target)
./rgb.py set <ch> <pct>  # set one channel, 0-100
./rgb.py all <pct>
./rgb.py off
./rgb.py probe           # walk channels one at a time to identify R/G/B
./rgb.py sweep
./rgb.py release         # reset and hand back to the stock firmware
```

`rgb.py` starts OpenOCD in the background and talks to it over its TCL RPC port
(6666).

---

## Power warning

The board is running on the Pi's 3.3 V rail alone. That is fine for the MCU and for
probing, but it will **not** drive real LED strips through the Q7xxx transistor bank
— those want the monitor's 12 V supply, and the Pi's 3V3 cannot source that current.

---

## Repository contents

| Path | Description |
|---|---|
| `rgb.py` | RGB controller — takes over the PWM channels over SWD |
| `uart_bridge.py` | TX/RX bridge to the MStar scaler over the Nuvoton's UART2 |
| `scan_periph.py` | Sweeps the peripheral address space for active blocks |
| `scan_all.py` | Forces all clocks on to reveal every implemented peripheral |
| `SCALER.md` | Investigation of the scaler link |
| `openocd/swd.cfg` | Minimal OpenOCD config for the SWD connection |
| `dumps/` | Flash and SRAM backups (see below) |

### Dumps

| File | Size | MD5 |
|---|---|---|
| `aprom_128k.bin` | 131072 | `c15c511b984512140c5f408eba4f0c0b` |
| `ldrom.bin` | 4096 | `446deaac0c6e35953486bf4551982933` |
| `sram.bin` | 20480 | `c5dec0b323a2ccdb89bafe71c2f052e4` |

These are unmodified stock MSI firmware images, kept here purely as a personal
recovery backup for this specific board — hence the private repository.
`ldrom.bin` is the APROM-aliasing artefact described above, not real LDROM content.
