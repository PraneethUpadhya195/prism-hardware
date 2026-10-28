"""Record INMP441 audio from the PRISM ESP32 capture firmware (prism_hardware.cpp, protocol v2).

Per take, written to inmp441_test_recordings/ next to this script:
  <stem>.wav          PRISM input: 8 kHz mono PCM16, near-DC removed (4th-order 20 Hz high-pass),
                      converted with saturation (never wrap-around)
  <stem>_nohp.wav     the same conversion without the high-pass (for audits)
  <stem>_raw_i32.npy  the raw 32-bit I2S words exactly as received
  <stem>.json         settings, checks, levels, position/note and key-press markers

Usage (close the Arduino Serial Monitor first):
  py record_audio.py                                   # one 10 s take on COM7
  py record_audio.py --takes 5 --position below --note "pMDI, protocol A"
  py record_audio.py --port COM5 --gain-db 12

While a take is recording, key presses in this console are saved as time markers
(about +/-0.1 s), for example: a = actuation, i = inhale start, o = inhale end,
e = exhale start.  Markers are the annotation the PRISM evaluation needs.

Requires: pyserial, numpy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import wave
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np
import serial

try:
    import msvcrt                  # Windows console key presses -> markers
except ImportError:                # other platforms: recording works, markers are disabled
    msvcrt = None

RECORDER_VERSION = "2.0.0"
FIRMWARE_MAJOR = "2"
DEFAULT_PORT = os.environ.get("PRISM_PORT", "COM7")
BAUD = 921600
OUTPUT_DIR = Path(__file__).resolve().parent / "inmp441_test_recordings"
POSITIONS = ("left", "below", "front", "inhaler", "other")

HIGHPASS_HZ = 20.0                 # removes DC, the start-up ramp and sub-audio airflow pressure
FULL_SCALE_24 = 2 ** 23            # INMP441 24-bit digital full scale
RATE_TOLERANCE = 0.02              # measured vs nominal sample rate
STARTUP_OFFSET_LIMIT = 0.03        # offset at t = 0 (fraction of full scale) that can count as a start-up ramp
REFERENCE_ACTIVE_LEVEL_DBFS = -12.8  # PRISM reference corpus, 95th-pct 20 ms RMS (>= 100 Hz); for orientation only
ERROR_MARKERS = (b"I2S_INIT_FAILED", b"I2S_DISCARD_FAILED", b"I2S_READ_FAILED")


# ── Serial link ──────────────────────────────────────────────────────────────

class Link:
    """Serial port with one buffer shared by text lines and the binary payload."""

    def __init__(self, port: str, baud: int) -> None:
        # Opening the port resets most ESP32 DevKit boards (DTR/RTS auto-reset).
        self.ser = serial.Serial(port, baud, timeout=0.2, write_timeout=2)
        self.buf = bytearray()
        self.received = 0

    def close(self) -> None:
        self.ser.close()

    def send(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def _fill(self) -> int:
        chunk = self.ser.read(max(1, min(self.ser.in_waiting, 65536)))
        self.buf += chunk
        self.received += len(chunk)
        return len(chunk)

    def line(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while True:
            i = self.buf.find(b"\n")
            if i >= 0:
                text = bytes(self.buf[:i]).strip()
                del self.buf[:i + 1]
                return text
            if time.monotonic() > deadline:
                return None
            self._fill()

    def wait_for(self, prefix: bytes, timeout: float) -> bytes:
        """Next line starting with ``prefix``; other lines (boot output) are ignored."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.line(max(0.0, deadline - time.monotonic()))
            if text is None:
                break
            if text.startswith(ERROR_MARKERS):
                raise RuntimeError(f"firmware reported: {text.decode(errors='replace')}")
            if text.startswith(prefix):
                return text
        raise TimeoutError(f"no {prefix.decode()} from the ESP32 within {timeout:.0f} s")

    def read_exact(self, n: int, idle_timeout: float, on_progress) -> bytes:
        out = bytearray()
        last = time.monotonic()
        while len(out) < n:
            if self.buf:
                take = min(n - len(out), len(self.buf))
                out += self.buf[:take]
                del self.buf[:take]
                last = time.monotonic()
                on_progress(len(out))
            elif self._fill() == 0 and time.monotonic() - last > idle_timeout:
                tail = bytes(out[-200:])
                why = " (firmware reported I2S_READ_FAILED)" if b"I2S_READ_FAILED" in tail else ""
                raise TimeoutError(f"incomplete audio: {len(out)} of {n} bytes{why}")
        return bytes(out)


def parse_fields(text: bytes) -> dict:
    return dict(item.split("=", 1) for item in text.decode().split()[1:] if "=" in item)


def connect(link: Link, boot_timeout: float = 20.0) -> dict:
    """Wait for the firmware's PRISM_CFG + READY (after its warm-up)."""
    link.send(b"I\n")                       # answered at once by a board that did not reset
    deadline = time.monotonic() + boot_timeout
    asked_again = False
    cfg = None
    while time.monotonic() < deadline:
        text = link.line(0.5)
        if text is None:
            if not asked_again and deadline - time.monotonic() < boot_timeout - 8:
                link.send(b"I\n")
                asked_again = True
            continue
        if text.startswith(ERROR_MARKERS):
            raise RuntimeError(f"firmware reported: {text.decode(errors='replace')}")
        if text.startswith(b"AUDIO_BEGIN"):          # v2 only streams after 'R'; v1 streams on every reset
            raise RuntimeError("The ESP32 is running the OLD v1 capture firmware: it started streaming a recording "
                               "as soon as the port opened (AUDIO_BEGIN without PRISM_CFG). Upload the new "
                               "prism_hardware.cpp (v2) to the board, then run this script again.")
        if text.startswith(b"PRISM_CFG"):
            cfg = parse_fields(text)
        elif text == b"READY" and cfg is not None:
            if not cfg.get("fw", "").startswith(FIRMWARE_MAJOR + "."):
                raise RuntimeError(f"firmware {cfg.get('fw')} does not speak protocol v{FIRMWARE_MAJOR}; "
                                   "upload the current prism_hardware.cpp")
            return cfg
    if link.received == 0:
        raise TimeoutError(f"Nothing at all was received in {boot_timeout:.0f} s. Check that this is the ESP32's COM "
                           "port, that the Serial Monitor is closed, and press the board's EN/RST button while this "
                           "script waits.")
    raise TimeoutError(f"Received {link.received} bytes but no PRISM_CFG/READY. Upload the new prism_hardware.cpp "
                       "(v2) at 921600 baud and run again.")


def record_take(link: Link, cfg: dict) -> dict:
    sr, n, bps = int(cfg["sample_rate"]), int(cfg["samples"]), int(cfg["bytes_per_sample"])
    while msvcrt is not None and msvcrt.kbhit():      # drop key presses made before the take
        msvcrt.getwch()
    link.send(b"R\n")
    link.wait_for(b"AUDIO_BEGIN", 5)
    started = datetime.now()
    t0 = time.monotonic()
    print(f"RECORDING {n / sr:.0f} s now. Key presses are logged as markers.")
    markers, tick = [], [1]

    def progress(nbytes: int) -> None:
        t = nbytes / (bps * sr)
        while msvcrt is not None and msvcrt.kbhit():
            key = msvcrt.getwch()
            markers.append({"t_s": round(t, 3), "key": key})
            print(f"  marker '{key}' at {t:.2f} s")
        while t >= tick[0]:
            print(f"  {tick[0]:>2d} s", end="\r", flush=True)
            tick[0] += 1

    payload = link.read_exact(n * bps, idle_timeout=3.0, on_progress=progress)
    wall = time.monotonic() - t0
    end = parse_fields(link.wait_for(b"AUDIO_END", 5))
    link.wait_for(b"READY", 5)
    print()
    return {"payload": payload, "end": end, "markers": markers, "wall_s": wall, "started": started}


# ── Checks on the raw I2S words ──────────────────────────────────────────────

def constant_low_bits(u: np.ndarray) -> int:
    """Number of low bits that are all-0 or all-1 together in >= 99% of words."""
    for k in range(16):
        mask = np.uint32((1 << (k + 1)) - 1)
        v = u & mask
        if np.mean((v == 0) | (v == mask)) < 0.99:
            return k
    return 16


def check_alignment(raw: np.ndarray) -> dict:
    """Where the INMP441's 24-bit sample sits in the 32-bit word.

    Expected: MSB-first in bits 31..8.  Bits 7..0 come from the released SD line:
    0 with a pull-down, or a copy of the last data bit when the line holds its
    level.  So 8 constant low bits (pull-down) or 9 (hold) mean aligned; one
    more means the data arrived one bit late (sign bit lost, loud sounds wrap);
    a bit 31 that disagrees with bit 30 on quiet samples means one bit early.
    """
    u = raw.view(np.uint32)
    k = constant_low_bits(u)
    low = u & np.uint32(0xFF)
    held = np.mean((low == 0) | (low == 0xFF))
    pulled_down = held > 0 and np.mean(low == 0) / held > 0.95
    expected = 8 if pulled_down else 9
    bit = {k_: (u >> np.uint32(k_)) & 1 for k_ in (28, 29, 30, 31)}
    # "quiet" from bits 30..28 only, so a corrupted bit 31 cannot hide itself; if bit 31 is the sign,
    # it equals bit 30 for every such sample below 7/8 of full scale.
    quiet = (bit[30] == bit[29]) & (bit[29] == bit[28])
    mismatch = float(np.mean(bit[31][quiet] != bit[30][quiet])) if quiet.any() else float("nan")
    info = {"constant_low_bits": k, "sd_released_level": "pulled down" if pulled_down else "held/other",
            "sign_bit_mismatch_quiet": mismatch}
    if mismatch > 0.05:
        return {**info, "status": "FAIL", "text": "bit 31 is not a sign bit: the data arrives one bit early or in the "
                                                  "wrong slot (check L/R pin, WS/SCK wiring)"}
    if k == expected:
        return {**info, "status": "PASS", "text": f"24-bit data in bits 31..8 (SD {info['sd_released_level']})"}
    if k == expected + 1:
        return {**info, "status": "FAIL", "text": "data is shifted one bit late: the sign bit is lost, so sounds above "
                                                  "half scale wrap around (I2S timing)"}
    return {**info, "status": "WARN", "text": f"{k} constant low bits (expected {expected}): the SD line may float "
                                              "between words; alignment cannot be confirmed (a pull-down on SD helps)"}


def dbfs(v: float) -> float:
    return float(20 * np.log10(v)) if v > 0 else float("-inf")


def analyse(raw: np.ndarray, payload: bytes, end: dict, sr: int) -> dict:
    checks = {}
    ok = len(raw) == int(end["samples"]) and (zlib.crc32(payload) & 0xFFFFFFFF) == int(end["crc32"], 16)
    checks["transfer"] = {"status": "PASS" if ok else "FAIL",
                          "text": f"{len(raw)} samples, CRC-32 {'ok' if ok else 'MISMATCH (bytes lost on serial)'}"}

    dt = ((int(end["t_last_us"]) - int(end["t_mark_us"])) % 2 ** 32) / 1e6
    rate = (int(end["n_last"]) - int(end["n_mark"])) / dt if dt > 0 else float("nan")
    rate_ok = abs(rate / sr - 1) <= RATE_TOLERANCE
    checks["sample_rate"] = {"status": "PASS" if rate_ok else "FAIL", "measured_hz": rate,
                             "text": f"measured {rate:.1f} Hz on the ESP32 clock (nominal {sr})"}

    checks["alignment"] = check_alignment(raw)

    s24 = (raw >> 8).astype(np.int64)
    wraps = int(np.sum(np.abs(np.diff(s24)) > FULL_SCALE_24))
    at_fs = int(np.sum(np.abs(s24) >= FULL_SCALE_24 - 256))
    checks["overload"] = {"status": "FAIL" if wraps else ("WARN" if at_fs else "PASS"),
                          "wrap_jumps": wraps, "samples_at_full_scale": at_fs,
                          "text": (f"{wraps} wrap-around jumps: the sound exceeded the microphone's range "
                                   "(too close, or airflow straight onto the port); redo further away or with a windscreen")
                          if wraps else f"0 wrap-around jumps, {at_fs} samples at full scale"}

    x = s24 / FULL_SCALE_24
    # Start-up ramp: offset at t = 0 (0.25 s moving average) against the typical low-frequency level in the
    # second half.  Slow airflow pressure also moves the offset, hence the comparison instead of a fixed limit.
    win = sr // 4
    c = np.cumsum(np.concatenate([[0.0], x]))
    lp = (c[win:] - c[:-win]) / win
    start = float(lp[0])
    typical = float(np.median(np.abs(lp[len(lp) // 2:]))) + 1e-4
    settling = abs(start) > STARTUP_OFFSET_LIMIT and abs(start) > 3 * typical
    checks["startup_offset"] = {"status": "WARN" if settling else "PASS", "start_offset_fs": start,
                                "typical_offset_fs": typical,
                                "text": f"offset at start {start:+.3f} FS vs typical {typical:.3f} FS later"
                                        + ("; still settling, raise WARMUP_MS in the firmware" if settling else "")}
    return {"checks": checks, "x": x}


# ── Conversion ───────────────────────────────────────────────────────────────

def _biquad(x: np.ndarray, b0: float, b1: float, b2: float, a1: float, a2: float) -> np.ndarray:
    out = np.empty(len(x))
    z1 = z2 = 0.0
    for i, v in enumerate(x.tolist()):
        o = b0 * v + z1
        z1 = b1 * v - a1 * o + z2
        z2 = b2 * v - a2 * o
        out[i] = o
    return out


def highpass(x: np.ndarray, sr: int, fc: float) -> np.ndarray:
    """Causal 4th-order Butterworth high-pass (two bilinear biquads); the same filter can run on a device."""
    y = np.asarray(x, dtype=np.float64) - np.median(x[: max(1, sr // 20)])   # avoid a start-up step
    w0 = 2 * np.pi * fc / sr
    c = np.cos(w0)
    for q in (0.5411961001, 1.3065629649):                                   # Butterworth pole-pair Qs
        alpha = np.sin(w0) / (2 * q)
        a0 = 1 + alpha
        y = _biquad(y, (1 + c) / 2 / a0, -(1 + c) / a0, (1 + c) / 2 / a0, -2 * c / a0, (1 - alpha) / a0)
    return y


def to_pcm16(y: np.ndarray) -> tuple[np.ndarray, int]:
    """Full scale 1.0 -> int16 with saturation; returns the number of saturated samples."""
    q = np.round(y * 32768.0)
    saturated = int(np.sum((q > 32767) | (q < -32768)))
    return np.clip(q, -32768, 32767).astype("<i2"), saturated


def levels(y: np.ndarray, sr: int) -> dict:
    n, h = int(0.02 * sr), int(0.01 * sr)
    frames = np.lib.stride_tricks.sliding_window_view(y, n)[::h]
    st = np.sqrt(np.mean(frames ** 2, axis=1))
    return {"peak_dbfs": dbfs(float(np.max(np.abs(y)))), "rms_dbfs": dbfs(float(np.sqrt(np.mean(y ** 2)))),
            "active_level_dbfs": dbfs(float(np.percentile(st, 95)))}


def write_wav(path: Path, pcm: np.ndarray, sr: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ── Main ─────────────────────────────────────────────────────────────────────

def save_take(take: dict, cfg: dict, args, stem: str) -> str:
    sr = int(cfg["sample_rate"])
    raw = np.frombuffer(take["payload"], dtype="<i4")
    result = analyse(raw, take["payload"], take["end"], sr)
    x, checks = result["x"], result["checks"]
    gain = 10 ** (args.gain_db / 20)
    y = highpass(x, sr, args.highpass_hz) if args.highpass_hz > 0 else x - np.mean(x)
    pcm, sat = to_pcm16(y * gain)
    pcm_nohp, sat_nohp = to_pcm16(x * gain)
    if sat:
        checks["saturation"] = {"status": "WARN", "saturated_samples": sat,
                                "text": f"{sat} samples saturated in the 16-bit file at gain {args.gain_db:+.1f} dB; lower --gain-db"}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wav, wav_nohp = out / f"{stem}.wav", out / f"{stem}_nohp.wav"
    write_wav(wav, pcm, sr)
    write_wav(wav_nohp, pcm_nohp, sr)
    np.save(out / f"{stem}_raw_i32.npy", raw)

    lv = levels(pcm.astype(np.float64) / 32768.0, sr)
    near_dc = dbfs(float(np.sqrt(np.mean((x - (y if args.highpass_hz > 0 else x)) ** 2))))
    status = "FAIL" if any(c["status"] == "FAIL" for c in checks.values()) else (
        "WARN" if any(c["status"] == "WARN" for c in checks.values()) else "PASS")
    meta = {
        "recorder_version": RECORDER_VERSION, "firmware": cfg, "recorded_at": take["started"].isoformat(timespec="milliseconds"),
        "files": {"wav": wav.name, "wav_nohp": wav_nohp.name, "raw": f"{stem}_raw_i32.npy"},
        "wav_sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
        "sample_rate": sr, "n_samples": int(len(raw)), "duration_s": len(raw) / sr,
        "position": args.position, "note": args.note, "markers": take["markers"],
        "conversion": {"source": "24-bit sample = raw_word >> 8; full scale 1.0 = INMP441 digital full scale",
                       "highpass": f"4th-order Butterworth {args.highpass_hz} Hz, causal" if args.highpass_hz > 0 else "none (mean removed)",
                       "gain_db": args.gain_db, "saturation": "clip, never wrap", "saturated_samples": sat,
                       "saturated_samples_nohp": sat_nohp},
        "levels_wav": lv, "near_dc_removed_dbfs": near_dc, "reference_active_level_dbfs": REFERENCE_ACTIVE_LEVEL_DBFS,
        "checks": checks, "status": status, "transfer_wall_s": take["wall_s"], "audio_end": take["end"],
    }
    (out / f"{stem}.json").write_text(json.dumps(meta, indent=2, default=float), encoding="utf-8")

    print(f"=== {stem}: {status} ===")
    for name, c in checks.items():
        print(f" [{c['status']}] {name}: {c['text']}")
    print(f" [INFO] levels ({args.highpass_hz:g} Hz high-pass, gain {args.gain_db:+.1f} dB): peak {lv['peak_dbfs']:.1f} dBFS, "
          f"RMS {lv['rms_dbfs']:.1f} dBFS, active {lv['active_level_dbfs']:.1f} dBFS "
          f"(PRISM reference recordings ~{REFERENCE_ACTIVE_LEVEL_DBFS:.0f} dBFS); near-DC removed {near_dc:.1f} dBFS")
    if take["markers"]:
        print(" [INFO] markers: " + ", ".join(f"{m['key']}@{m['t_s']:.2f}s" for m in take["markers"]))
    print(f" Saved {wav}")
    if status == "FAIL":
        print(" -> Do not use this take for PRISM evaluation; record it again.")
    return status


def main() -> int:
    ap = argparse.ArgumentParser(description="Record INMP441 audio from the PRISM ESP32 firmware (protocol v2).")
    ap.add_argument("--port", default=DEFAULT_PORT, help=f"serial port (default {DEFAULT_PORT}, or PRISM_PORT)")
    ap.add_argument("--takes", type=int, default=1, help="number of recordings in this session")
    ap.add_argument("--position", choices=POSITIONS, help="microphone position, saved in the file name and metadata")
    ap.add_argument("--note", default="", help="free text saved in the metadata")
    ap.add_argument("--gain-db", type=float, default=0.0, help="digital gain before 16-bit conversion (saturates, never wraps)")
    ap.add_argument("--highpass-hz", type=float, default=HIGHPASS_HZ, help="high-pass cut-off for the main WAV (0 = only remove the mean)")
    ap.add_argument("--out", default=str(OUTPUT_DIR), help="output folder")
    args = ap.parse_args()

    print(f"Connecting to {args.port} at {BAUD} baud (the ESP32 resets and warms up for a few seconds)...")
    link = Link(args.port, BAUD)
    statuses = []
    try:
        cfg = connect(link)
        print(f"Firmware {cfg['fw']}: {cfg['sample_rate']} Hz, {int(cfg['samples']) / int(cfg['sample_rate']):.0f} s per take, "
              f"warm-up {cfg['warmup_ms']} ms")
        for k in range(1, args.takes + 1):
            if args.takes > 1:
                input(f"\nTake {k}/{args.takes}: press Enter to start recording...")
            take = record_take(link, cfg)
            stem = "inmp441_" + take["started"].strftime("%Y%m%d_%H%M%S_%f")
            stem += f"_{args.position}" if args.position else ""
            statuses.append(save_take(take, cfg, args, stem))
    finally:
        link.close()
    return 1 if "FAIL" in statuses else 0


if __name__ == "__main__":
    sys.exit(main())
