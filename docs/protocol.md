# FitTrack Dara — Bluetooth LE protocol

Reverse-engineered live on 2026-08-25 (GATT dump + notify captures in `captures/`),
cross-checked against openScale's `FitTrackDaraHandler.kt` and `MGBHandler.kt` (GPLv3).
This unit is an eLink/Icomon "MGB family" device: it advertises as **`SWAN`** (not
"FitTrack"), but speaks the same protocol as the Dara.

## Advertisement

| field | value |
|---|---|
| local name | `SWAN` (this unit; siblings use `FitTrack`, `icomon`, `yg`) |
| service UUIDs | `0xFFB0`, `0xD618` |
| manufacturer data | company id `0xFFAC`: `69 CD 05 EC B3 07 00` |

## GATT (service `0xFFB0`)

| char | props | role |
|---|---|---|
| `0xFFB1` | write-without-response | app → scale commands/config |
| `0xFFB2` | notify | scale → app telemetry (all frames below) |
| `0xFFB3` | read, notify, write | undocumented; reads 0 bytes, notifies `01` on session start. No data seen. |

A second service `d618d000-6000-1000-8000-000000000000` exists (write + notify char);
unused by this protocol, believed OTA/vendor.

**No standard Battery Service (`0x180F`) and no battery value observed anywhere** →
battery level is not obtainable from this hardware as far as we can tell.

## Framing

8-byte control/telemetry frame:

```
AC 02 b2 b3 b4 b5 CHAN CKSUM
CKSUM = (b2 + b3 + b4 + b5 + CHAN) & 0xFF
```

`CHAN` selects the stream:

| chan | direction | meaning |
|---|---|---|
| `0xCC` | both | command / handshake / echo |
| `0xCE` | scale→app | live (unstable) weight |
| `0xCA` | scale→app | stabilised weight |
| `0xCB` | scale→app | body-composition dump / impedance |

### Commands (app → scale, written to FFB1)

Init sequence replayed from vendor-app capture (works):

```
AC 02 F7 00 00 00 CC ..   magic init #1
AC 02 FA 00 00 00 CC ..   magic init #2
AC 02 FB SEX AGE HEIGHT   user profile: sex 1=male 2=female, age years, height cm
AC 02 FD YY MM DD         date (yy = year-2000)
AC 02 FC HH MM SS         time
AC 02 FE 06 UNIT 00       display unit: KG=0 LB=1 ST=2  (driver always sets KG)
```

The vendor app additionally performs an encrypted challenge/response
(`AE 03…` / `AD 01…`) which is *not* needed for weight or body composition.
Command echoes come back on FFB2 with chan `CC`.

### Weight frames

Weight lives in `b2 b3`, **big-endian, tenths of a display unit** (kg here):
example: live `AC 02 02 EE 00 00 CE BE` → 75.0 kg; stable `AC 02 02 EE 00 00 CA BA` → 75.0 kg.
Frames with value ≤ 0 or ≥ 6000 are idle/noise.

> Note: MGB-family firmware variants exist that stream weight in 0.01 kg steps;
> this unit uses 0.1 steps like the Dara handler expects.

### Impedance frame

`AC 02 FD 01 HI LO CB ..` → whole-body impedance in ohms, big-endian
(example: `FD 01 02 BC CB` = 700 Ω).

### Body-composition dump

After the weight settles and impedance is measured, the scale emits per-index
value pairs followed by an end marker:

```
AC 02 FE IDX HI LO CB CKSUM      (values big-endian)
AC 02 FE FC 00 00 CB CKSUM       end marker (idx 0xFC)
```

Index map (factors verified on hardware):

| idx | metric | factor | captured |
|----|------------------|-------|---------|
| 0x00 | weight | ÷10 kg | 750 → 75.0 |
| 0x01 | BMI | ÷10 | e.g. 239 → 23.9 |
| 0x02 | body fat | ÷10 % | e.g. 187 → 18.7 |
| 0x03 | subcutaneous fat | ÷10 % | — |
| 0x04 | visceral fat | int index | — |
| 0x05 | lean mass | ÷10 kg | — |
| 0x06 | BMR | int kcal | — |
| 0x07 | bone mass | ÷10 kg | — |
| 0x08 | body water | ÷10 % | — |
| 0x09 | metabolic age | int years | — |
| 0x0A | protein | ÷10 % | — |
| 0xFC | end marker | — | |

If no electrode contact was made the dump still arrives with zeros/absent values —
treat 0 as "no data" for every metric except weight.

### Composite frames (redundant mirror, used as completion signal)

Two 20-byte notifications repeat the same data after the dump:

```
AC 02 FF 00 02 21 <yy mm dd hh mm ss> <weight BE> <BMI BE> <fat BE> <subcut BE>
01 00 ... <lean BE> <bmr BE> <bone BE> <water BE> <age u8> <protein BE> ... <impedance BE?>
```

Layout drifts slightly between firmware variants; only the per-index frames above
are authoritative. Arrival of the second composite frame = measurement complete.

## Session flow

1. Scale sleeps until woken by weight. Advertises ~60 s.
2. Connect, enable notifications on FFB2 (FFB3 optional), send init sequence (~250 ms apart).
3. User steps on: `CE` live frames every ~150 ms, then `CA` stable frame.
4. Impedance (`FD`) + dump indices (`FE … CB`), end marker, composites, done.
5. Multiple consecutive weigh-ins can occur on one connection.
6. Driver publishes once complete (or after grace timeout with weight-only) and disconnects.
