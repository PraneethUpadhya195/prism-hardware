import serial
import numpy as np
import wave
import time
from pathlib import Path
from datetime import datetime

PORT = "COM7"
BAUD = 921600
SAMPLE_RATE = 8000
RECORD_SECONDS = 15
EXPECTED_BYTES = SAMPLE_RATE * RECORD_SECONDS * 4

# Create recordingds folder in the current working directory
OUTPUT_DIR = Path("recordingds")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Unique timestamp-based filename
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUT_FILE = OUTPUT_DIR / f"inmp441_{timestamp}.wav"

print(f"Connecting to ESP32 on {PORT}...")

with serial.Serial(PORT, BAUD, timeout=10) as ser:
    # Opening the port may reset the ESP32.
    print("Waiting for ESP32...")
    deadline = time.time() + 20
    ready = False

    while time.time() < deadline:
        line = ser.readline().strip()

        if line == b"AUDIO_BEGIN":
            ready = True
            break

    if not ready:
        raise RuntimeError(
            "AUDIO_BEGIN not received. Check the COM port, "
            "upload the sketch again, and retry."
        )

    print("Recording received. Reading audio data...")

    audio = bytearray()

    while len(audio) < EXPECTED_BYTES:
        chunk = ser.read(min(4096, EXPECTED_BYTES - len(audio)))

        if not chunk:
            break

        audio.extend(chunk)

if len(audio) != EXPECTED_BYTES:
    raise RuntimeError(
        f"Incomplete recording: received {len(audio)} "
        f"of {EXPECTED_BYTES} bytes."
    )

# Interpret ESP32 samples as signed 32-bit integers
samples = np.frombuffer(audio, dtype="<i4")

# Convert to signed 16-bit PCM
pcm = (samples >> 16).astype(np.int16)

# Save WAV without overwriting previous recordings
with wave.open(str(OUTPUT_FILE), "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(SAMPLE_RATE)
    wav.writeframes(pcm.astype("<i2").tobytes())

print(f"\nSaved: {OUTPUT_FILE.resolve()}")
print(f"Duration: {len(pcm) / SAMPLE_RATE:.2f} seconds")
print(f"Sample min/max: {pcm.min()} / {pcm.max()}")
print(f"Recordings folder: {OUTPUT_DIR.resolve()}")
print("Open the WAV file and listen to the recording.")