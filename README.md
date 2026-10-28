# PRISM Hardware — ESP32 Audio Capture Platform

Hardware configuration for the **PRISM** (Pulmonary Response and Inhaler System Monitor) project.

This repository contains the **ESP32 capture firmware**, the **host-side recording tools** and the
**audio analysis utilities** that form the hardware acquisition layer of the PRISM smart inhaler
platform.  The firmware records audio from an INMP441 I2S MEMS microphone on an ESP32 DevKit V1 and
streams it over USB serial to a host computer, where Python scripts receive, verify and convert the
audio into WAV files for inspection and evaluation.

---

## Contents

1. [Hardware overview](#hardware-overview)
2. [Components](#components)
3. [Wiring](#wiring)
4. [Firmware](#firmware)
5. [Serial protocol](#serial-protocol)
6. [Host recording script](#host-recording-script)
7. [Audio analysis script](#audio-analysis-script)
8. [I2S configuration](#i2s-configuration)
9. [Sample conversion](#sample-conversion)
10. [Integrity checks](#integrity-checks)
11. [Repository structure](#repository-structure)
12. [Prerequisites](#prerequisites)
13. [Setup and usage](#setup-and-usage)
14. [Current limitations](#current-limitations)

---

## Hardware overview

The PRISM hardware captures acoustic data from a pressurised metered-dose inhaler (pMDI) using a
digital MEMS microphone connected to an ESP32 microcontroller.  The ESP32 reads audio samples over
the I2S bus and transmits them to a host PC over USB serial.  A Python script on the host receives
the raw 32-bit I2S words, verifies the transfer with a CRC-32 checksum, checks bit alignment and
sample rate, converts the audio to 16-bit PCM, applies a high-pass filter to remove near-DC content,
and saves the result as a WAV file.

```
INMP441 (I2S) ──► ESP32 DevKit V1 ──► USB Serial (921600 baud) ──► Host PC (Python)
                                                                        │
                                                                        ├── .wav   (8 kHz mono PCM16, high-passed)
                                                                        ├── _nohp.wav (without high-pass)
                                                                        ├── _raw_i32.npy (raw 32-bit I2S words)
                                                                        └── .json  (metadata, checks, markers)
```

---

## Components

| Component | Description |
|---|---|
| **ESP32 DevKit V1** (38-pin) | Microcontroller with I2S peripheral and USB-to-serial bridge |
| **INMP441** | I2S MEMS microphone; 24-bit output, supports up to 104 kHz sample rate |
| **Breadboard and jumper wires** | Prototyping connections between the ESP32 and the INMP441 |
| **USB cable** | Powers the ESP32 and provides the serial data link to the host PC |
| **pMDI** | Pressurised metered-dose inhaler (the device under test) |

---

## Wiring

| INMP441 Pin | ESP32 Pin | Notes |
|---|---|---|
| VDD | 3V3 | 3.3 V power supply from the ESP32 |
| GND | GND | Common ground |
| SCK | GPIO 26 | I2S bit clock (BCLK) |
| WS | GPIO 25 | I2S word select (LRCLK) |
| SD | GPIO 33 | I2S serial data input |
| L/R | GND | Selects the left channel |

Grounding the L/R pin configures the INMP441 to output on the left I2S channel.  The firmware
reads the left slot only.

---

## Firmware

The capture firmware is in [`prism_hardware.cpp`](prism_hardware.cpp).  It is built with the
**Arduino-ESP32 core 3.x** (`ESP_I2S` library) and runs on an ESP32 DevKit V1.

### Key parameters

| Parameter | Value | Notes |
|---|---|---|
| Firmware version | `2.1.0` | Protocol v2 |
| Sample rate | 8000 Hz | Required by the PRISM detector |
| Recording length | 10 seconds (80 000 samples) | Configurable in firmware |
| I2S bit depth | 32-bit (24-bit data from the INMP441) | Raw words sent to the host |
| Serial baud rate | 921 600 | ~92 kB/s available; ~32 kB/s needed |
| Warm-up | 4 000 ms discarded after power-up | Eliminates the INMP441 start-up offset ramp |
| I2S block size | 256 samples (32 ms at 8 kHz) | Per DMA read |
| RX bit shift | Disabled | Corrects a one-bit-late alignment issue (see v2.1.0 notes) |

### Boot sequence

1. Serial port opens at 921 600 baud.
2. I2S peripheral is initialised with the INMP441 pin configuration.
3. The RX slot is reconfigured to disable the Philips bit shift.
4. 4 seconds of audio are read and discarded (warm-up).
5. `PRISM_CFG` and `READY` are sent on the serial line.

### Recording

- The host sends `R\n` to start a recording.
- The firmware streams raw 32-bit I2S words as binary data between `AUDIO_BEGIN` and `AUDIO_END`
  markers.
- Between recordings the I2S DMA buffer is drained continuously to keep the microphone clocked and
  settled.

### Firmware version history

| Version | Change |
|---|---|
| **2.1.0** | RX bit shift disabled — corrects a one-bit-late alignment where the sign bit was lost, causing loud sounds to wrap around. Verified on hardware: alignment, rate (7 992.9 Hz), CRC and start-up checks all pass. |
| **2.0.0** | Protocol v2: host-triggered recording (`R`), `PRISM_CFG` announcement, CRC-32 and timing stamps in `AUDIO_END`, raw 32-bit I2S words (no shift or gain on the ESP32 side). |

---

## Serial protocol

Text lines end with `\r\n`.  Other output (e.g. ESP32 boot ROM messages) is ignored by the host.

| Direction | Message | Description |
|---|---|---|
| ESP32 → Host | `I2S_READY` | I2S peripheral initialised successfully |
| ESP32 → Host | `PRISM_CFG fw=… sample_rate=… samples=… …` | Firmware configuration key-value pairs |
| ESP32 → Host | `READY` | Board is idle and ready for a command |
| Host → ESP32 | `R\n` | Start a recording |
| ESP32 → Host | `AUDIO_BEGIN` | Binary audio payload follows |
| ESP32 → Host | *(binary: N × int32, little-endian)* | Raw 32-bit I2S words |
| ESP32 → Host | `AUDIO_END samples=… crc32=… n_mark=… t_mark_us=… …` | End-of-recording with CRC-32 and timing data |
| Host → ESP32 | `I\n` | Re-announce configuration (no reset needed) |
| ESP32 → Host | `I2S_INIT_FAILED` / `I2S_DISCARD_FAILED` | Fatal fault (repeated every second) |
| ESP32 → Host | `I2S_READ_FAILED captured=N` | Mid-recording I2S error; the host discards the take |

### `AUDIO_END` fields

| Field | Content |
|---|---|
| `samples` | Number of samples sent |
| `crc32` | CRC-32 (IEEE 802.3) of the binary payload |
| `n_mark` / `t_mark_us` | Sample count and `micros()` timestamp at the 1 s timing mark |
| `n_last` / `t_last_us` | Final sample count and `micros()` timestamp |
| `min` / `max` | Minimum and maximum raw I2S word values in the recording |

The timing marks allow the host to compute the actual sample rate on the ESP32 clock.

---

## Host recording script

[`record_audio.py`](record_audio.py) connects to the ESP32 over USB serial and manages recording
sessions.

### Usage

```bash
# Prerequisites
pip install pyserial numpy

# One 10-second take on the default port (COM7, or set PRISM_PORT)
py record_audio.py

# Multiple takes with metadata
py record_audio.py --takes 5 --position below --note "pMDI, protocol A"

# Custom port and gain
py record_audio.py --port COM5 --gain-db 12
```

> **Note:** Close the Arduino Serial Monitor before running the script.

### Command-line options

| Option | Default | Description |
|---|---|---|
| `--port` | `COM7` (or `PRISM_PORT` env var) | ESP32 serial port |
| `--takes` | `1` | Number of recordings per session |
| `--position` | *(none)* | Microphone position: `left`, `below`, `front`, `inhaler`, `other` |
| `--note` | `""` | Free-text note saved in the metadata |
| `--gain-db` | `0.0` | Digital gain before 16-bit conversion (saturates, never wraps) |
| `--highpass-hz` | `20.0` | High-pass cut-off for the main WAV (0 = only remove the mean) |
| `--out` | `inmp441_test_recordings/` | Output folder |

### Key-press markers

While a take is recording, key presses in the console are saved as timestamped markers in the
metadata JSON (approximately ±0.1 s accuracy).  Example markers:

- `a` — actuation
- `i` — inhale start
- `o` — inhale end
- `e` — exhale start

### Output files (per take)

| File | Content |
|---|---|
| `<stem>.wav` | 8 kHz mono PCM16, high-passed (4th-order Butterworth at 20 Hz), with saturation |
| `<stem>_nohp.wav` | Same conversion without the high-pass filter (for audits) |
| `<stem>_raw_i32.npy` | Raw 32-bit I2S words exactly as received from the ESP32 |
| `<stem>.json` | Full metadata: firmware config, checks, levels, markers, conversion settings |

---

## Audio analysis script

[`analyse_audio.py`](analyse_audio.py) provides quick visual inspection of a recorded WAV file.

It generates three plots:
1. **Full waveform** — the complete recording (time vs. amplitude).
2. **25 ms segment** — a zoomed view to inspect individual wave cycles.
3. **RMS energy** — signal energy in 100 ms windows over the recording duration.

### Usage

```bash
pip install matplotlib
py analyse_audio.py
```

The script reads `inmp441_recording.wav` from the repository root by default.

---

## I2S configuration

| Parameter | Value |
|---|---|
| Mode | I2S Standard (Philips) |
| Sample rate | 8 000 Hz |
| Data width | 32-bit per DMA word |
| Slot mode | Mono, left channel |
| BCLK pin | GPIO 26 |
| WS pin | GPIO 25 |
| Data-in pin | GPIO 33 |
| RX bit shift | Disabled (fw ≥ 2.1.0) |
| I2S timeout | 1 000 ms |

The INMP441 outputs 24-bit data left-justified in the 32-bit I2S word.  The useful sample occupies
bits 31–8.  Bits 7–0 come from the released SD line (typically zero if pulled down).

---

## Sample conversion

The ESP32 sends raw 32-bit I2S words unchanged.  All conversion happens on the host:

```
int32_t raw   = dma_word;                          // 32-bit I2S word from the ESP32
int32_t s24   = raw >> 8;                           // right-shift to extract the 24-bit sample
float   norm  = (float)s24 / (float)(1 << 23);     // normalise to [-1.0, 1.0]
int16_t pcm16 = clip(round(norm * 32768), -32768, 32767);  // 16-bit PCM with saturation
```

The host script then applies a **4th-order Butterworth high-pass filter** at 20 Hz (causal, two
bilinear biquad sections) to remove DC offset and the INMP441's start-up ramp.

- Conversion uses **saturation** (clipping), never wrap-around.
- An optional digital gain (`--gain-db`) is applied before the 16-bit quantisation.

---

## Integrity checks

The host recording script performs the following checks on every take:

| Check | What it verifies |
|---|---|
| **Transfer** | Sample count matches `AUDIO_END`, CRC-32 of the payload matches the firmware's value |
| **Sample rate** | Measured rate from the ESP32's `micros()` timing stamps is within 2 % of the 8 000 Hz nominal |
| **Bit alignment** | The INMP441's 24-bit data sits in bits 31–8; detects if the data arrived one bit late (sign-bit loss) or one bit early |
| **Overload** | Wrap-around jumps in the 24-bit range (sound exceeded the microphone's dynamic range) |
| **Start-up offset** | DC offset at the beginning of the recording compared to the typical level later; warns if the INMP441 was still settling |
| **Saturation** | Number of 16-bit samples clipped during conversion at the applied gain |

Each check reports `PASS`, `WARN` or `FAIL`.  The overall take status is `FAIL` if any check fails.
Failed takes should not be used for PRISM evaluation.

---

## Repository structure

```
prism-hardware/
├── README.md                   # This file
├── prism_hardware.cpp          # ESP32 capture firmware (Arduino-ESP32 core 3.x)
├── record_audio.py             # Host recording script (serial protocol v2)
├── analyse_audio.py            # Quick WAV waveform and RMS visualisation
├── inmp441_recording.wav       # Sample recording (8 kHz mono PCM16)
├── IMG_9168.JPG.jpeg           # Photo of the hardware setup
├── log.md                      # Development and testing log
├── App.md                      # README for the mobile application (separate component)
├── ML.md                       # README for the ML pipeline (separate component)
├── .gitignore
├── plot/                       # Waveform and RMS analysis plots
│   ├── Figure_1.png            # Full waveform plot
│   ├── Figure_2.png            # 25 ms waveform segment
│   ├── Figure_3.png            # RMS energy over time
│   └── inhalation/             # Inhalation test plots
└── recordingds/                # Test recordings (git-ignored)
```

---

## Prerequisites

| Requirement | Details |
|---|---|
| **ESP32 DevKit V1** | 38-pin variant with USB-to-serial bridge |
| **INMP441 MEMS microphone** | Wired as shown in [Wiring](#wiring) |
| **Arduino IDE** | Version 2.3.10 or later |
| **Arduino-ESP32 board package** | Version 3.3.12 or later (provides `ESP_I2S.h`) |
| **Python** | 3.8 or later |
| **pyserial** | `pip install pyserial` |
| **numpy** | `pip install numpy` |
| **matplotlib** | `pip install matplotlib` (for the analysis script) |

---

## Setup and usage

### 1. Flash the firmware

1. Open [`prism_hardware.cpp`](prism_hardware.cpp) in the Arduino IDE.
2. Select **ESP32 Dev Module** as the board.
3. Set the upload speed to **921 600** baud.
4. Upload to the ESP32.

### 2. Record audio

1. **Close the Arduino Serial Monitor** (only one program can hold the COM port).
2. Connect the ESP32 via USB.
3. Run the recording script:

```bash
py record_audio.py
```

4. The script connects to the ESP32, waits for the 4-second warm-up, and then records a 10-second
   take.
5. Output files are written to `inmp441_test_recordings/`.

### 3. Analyse the recording

```bash
py analyse_audio.py
```

This displays waveform and RMS plots for visual inspection of the captured audio.

### 4. Multi-take sessions

```bash
py record_audio.py --takes 5 --position below --note "pMDI test session"
```

Between takes, the script prompts you to press Enter.  Key-press markers can be entered during each
recording to annotate acoustic events.

---

## Current limitations

- **USB serial only.** Audio is transferred over USB serial, not Bluetooth Low Energy.  The BLE
  recorder firmware is a separate component.
- **No real-time streaming.** The firmware records a fixed-length take and transfers it in full after
  recording completes.
- **Sample rate.** The INMP441 is clocked at 8 000 Hz.  The measured ESP32 I2S clock rate is
  approximately 7 993 Hz (within the 2 % tolerance).
- **No battery monitoring.** The ESP32 does not report battery level or power status.
- **Prototype wiring.** The current setup uses breadboard connections and jumper wires; no PCB or
  enclosure has been designed.
- **Microphone placement.** Testing has been performed with the microphone held near the inhaler, not
  in a finalised inhaler-mounted configuration.
- **No frequency-response or sensitivity calibration.** The INMP441's characteristics have not been
  measured against a reference.

---

*PRISM — Capstone Project 2026*
