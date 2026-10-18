import wave
import numpy as np
import matplotlib.pyplot as plt

FILE = "inmp441_recording.wav"

# Load the WAV file
with wave.open(FILE, "rb") as wav:
    sample_rate = wav.getframerate()
    channels = wav.getnchannels()
    sample_width = wav.getsampwidth()
    frames = wav.readframes(wav.getnframes())

# This recording script should have produced mono, 16-bit PCM.
if channels != 1 or sample_width != 2:
    raise ValueError(
        f"Unexpected format: {channels} channel(s), "
        f"{sample_width * 8}-bit samples"
    )

audio = np.frombuffer(frames, dtype="<i2")
time = np.arange(len(audio)) / sample_rate

print(f"Sample rate: {sample_rate} Hz")
print(f"Duration: {len(audio) / sample_rate:.2f} seconds")
print(f"Minimum amplitude: {audio.min()}")
print(f"Maximum amplitude: {audio.max()}")
print(f"Peak amplitude: {np.max(np.abs(audio.astype(np.int32)))}")

# Plot the complete recording
plt.figure(figsize=(12, 4))
plt.plot(time, audio)
plt.xlabel("Time (seconds)")
plt.ylabel("Amplitude")
plt.title("INMP441 Audio Waveform")
plt.grid(True)
plt.tight_layout()
plt.show()

# Plot a short segment to inspect individual wave cycles
start = 1.0
duration = 0.025  # 25 milliseconds

begin = int(start * sample_rate)
end = min(int((start + duration) * sample_rate), len(audio))

plt.figure(figsize=(12, 4))
plt.plot(time[begin:end], audio[begin:end])
plt.xlabel("Time (seconds)")
plt.ylabel("Amplitude")
plt.title("INMP441 Waveform — 25 ms Segment")
plt.grid(True)
plt.tight_layout()
plt.show()

# Calculate RMS in 100 ms windows
window_size = int(0.1 * sample_rate)
usable = audio[:len(audio) // window_size * window_size]
windows = usable.reshape(-1, window_size).astype(np.float64)

rms = np.sqrt(np.mean(windows**2, axis=1))
rms_time = (np.arange(len(rms)) + 0.5) * 0.1

plt.figure(figsize=(12, 4))
plt.plot(rms_time, rms)
plt.xlabel("Time (seconds)")
plt.ylabel("RMS amplitude")
plt.title("Audio Energy Over Time")
plt.grid(True)
plt.tight_layout()
plt.show()
