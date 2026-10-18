# PRISM — INMP441 Microphone Testing Log

## 1. Objective

Test the INMP441 I2S MEMS microphone with an ESP32 DevKit V1 and establish a working audio acquisition pipeline for the PRISM smart inhaler project.

The initial goal was to verify that the microphone could capture sound. After that, the goal was to record audio on the ESP32, transfer it to a Windows laptop, and inspect the captured signal using Python.

## 2. Hardware Setup

### Components
- ESP32 DevKit V1 (38-pin)
- INMP441 I2S MEMS microphone
- Breadboard and jumper wires
- USB connection to a Windows laptop

### Wiring

| INMP441 Pin | ESP32 Pin |
|---|---|
| VDD | 3V3 |
| GND | GND |
| SCK | GPIO 26 |
| WS | GPIO 25 |
| SD | GPIO 33 |
| L/R | GND |

The microphone is powered using the ESP32's 3.3 V output.

Grounding L/R selects the microphone's left-channel output.

## 3. Development Environment

- Arduino IDE: 2.3.10
- Arduino-ESP32 board package: 3.3.12
- Python on Windows
- Python libraries: `pyserial`, `numpy`, `matplotlib`
- Audio sample rate: 16,000 Hz

## 4. Initial I2S Communication Test

### Objective
Verify that the ESP32 could initialize the I2S interface and receive data from the INMP441.

### Implementation
The ESP32 was programmed using the `ESP_I2S.h` library available in the installed Arduino-ESP32 core.

The I2S configuration used:
- Standard I2S mode
- 16 kHz sample rate
- 32-bit data width
- Mono mode
- BCLK on GPIO 26
- WS on GPIO 25
- Data input on GPIO 33

The initial test calculated peak amplitude and RMS from blocks of 256 samples.

### Initial Observation
The first configuration selected the right channel, while the microphone's L/R pin was grounded.

The resulting measurements were almost entirely zero.

### Correction
The channel selection was changed from:

`I2S_STD_SLOT_RIGHT`

to:

`I2S_STD_SLOT_LEFT`

No wiring changes were required.

### Result
The microphone subsequently produced non-zero readings. Peak and RMS values varied over time, indicating that the acquisition path was responding to sound.

A test was performed by keeping the microphone silent, clapping, and then returning to silence.

The observed measurements changed during the test, confirming a response to acoustic events.

**Status:** Basic microphone response verified. Exact sample decoding and audio fidelity still require validation.

## 5. Five-Second Audio Capture

### Objective
Capture a short audio recording on the ESP32 and transfer it to the laptop for analysis.

### ESP32 Implementation
The sketch was configured to:
- Initialize I2S at 16 kHz.
- Read 32-bit samples in blocks of up to 256 samples.
- Capture 80,000 samples.
- Transmit the samples through USB Serial.
- Use a serial baud rate of 921600.
- Print an `AUDIO_BEGIN` marker before transmitting binary audio data.

The nominal recording duration is:

80,000 / 16,000 = 5 seconds

The expected raw payload size is:

80,000 × 4 bytes = 320,000 bytes

### Recording Procedure
1. Upload the capture sketch to the ESP32.
2. Close Arduino IDE Serial Monitor.
3. Run a Python script on Windows to open the ESP32's COM port.
4. Wait for the `AUDIO_BEGIN` marker.
5. Receive the binary audio payload.
6. Interpret the payload as signed 32-bit integers.
7. Convert the samples to 16-bit PCM.
8. Save the result as `inmp441_recording.wav`.

### Python Dependencies

Install the required packages with:

`py -m pip install pyserial numpy`

### Sample Conversion
The capture script used the following conversion:

`pcm = (samples >> 16).astype(np.int16)`

This assumes that the useful microphone data is aligned in the upper bits of each 32-bit sample.

**Important:** This conversion has not yet been independently validated. The resulting WAV file must be inspected for correct amplitude, waveform structure, and playback quality.

### Result
The WAV recording was generated successfully and the user confirmed that the recording process worked.

**Status:** Audio capture and transfer pipeline operational; sample-format validation pending.

## 6. Audio Analysis Using Python

### Objective
Inspect the recorded waveform, identify acoustic events, and assess the captured audio before integrating it into the PRISM pipeline.

### Dependencies

Install Matplotlib with:

`py -m pip install matplotlib`

The analysis script uses:
- `wave` to read the WAV file.
- `numpy` to process the samples.
- `matplotlib` to visualize the signal.

### Analysis Performed

#### 6.1 File Metadata
The script reads:
- Sample rate
- Number of channels
- Sample width
- Total number of samples
- Recording duration

It also prints the minimum and maximum PCM amplitudes.

#### 6.2 Complete Waveform
The complete recording is plotted with time on the x-axis and amplitude on the y-axis.

This plot helps identify:
- Quiet sections
- Speech or other sustained acoustic events
- Short transients such as claps
- Potential clipping

#### 6.3 Short Waveform Segment
A 25 ms segment is plotted to inspect the signal at a finer timescale.

This can help reveal waveform structure and possible decoding problems.

#### 6.4 RMS Energy
The recording is divided into 100 ms windows.

RMS is calculated for each window:

`RMS = sqrt(mean(samples²))`

The resulting energy curve shows how the signal's amplitude changes throughout the recording.

### Expected Observations
- A clap should produce a brief increase in amplitude.
- Speech should produce time-varying activity.
- Quiet sections should generally have lower RMS energy.
- Repeated peaks at the maximum or minimum PCM values may indicate clipping.

### Current Status
The Python analysis script has been provided.

The plots still need to be inspected to determine whether the audio has the expected waveform structure and whether the sample conversion is correct.

## 7. Current Progress Summary

| Stage | Status |
|---|---|
| ESP32 setup and I2S initialization | Completed |
| Initial microphone response test | Completed |
| Left-channel selection | Completed |
| Silence/clap/silence test | Completed |
| Five-second audio capture | Completed |
| Serial transfer to Windows | Completed |
| WAV file generation | Completed |
| Full waveform inspection | Pending review |
| Sample alignment and conversion validation | Pending |
| Playback quality assessment | Pending |
| Continuous audio streaming | Not started |
| PRISM event-detection integration | Not started |

## 8. Next Steps

1. Run `analyze_audio.py` on `inmp441_recording.wav`.
2. Inspect the complete waveform, 25 ms segment, and RMS curve.
3. Listen to the recording and check for distortion or excessive noise.
4. Verify the 32-bit I2S sample alignment and the 16-bit PCM conversion.
5. Confirm the effective sample rate and recording duration.
6. Once the signal is validated, investigate continuous audio streaming.
7. Integrate the validated audio acquisition path into the PRISM application.

## 9. Important Limitations

- Non-zero I2S readings demonstrate activity but do not, by themselves, prove accurate audio decoding.
- Successful WAV generation does not guarantee that the WAV contains correctly scaled audio.
- No quantitative microphone sensitivity, frequency-response, or noise-floor measurements have been performed.
- The microphone has not yet been tested as part of the final inhaler hardware assembly.
- The current tests do not establish inhalation-technique classification accuracy.

## INMP441 Audio Capture — Initial Waveform Inspection

### Objective
Inspect the captured 10-second WAV recording to assess whether the INMP441
produces measurable acoustic activity and whether the recording is suitable
for further audio processing.

### Test configuration
- Sample rate: 16,000 Hz
- Recording duration: 10 seconds
- Channels: Mono
- WAV format: 16-bit PCM
- Source samples: Signed 32-bit I2S words
- Conversion used: `(samples >> 16).astype(np.int16)`

### Observations
- The full waveform contains multiple regions of differing signal amplitude.
- A large transient occurs at approximately 6.7 seconds, reaching roughly
  +/-32,000 in the 16-bit waveform.
- Smaller bursts of activity appear around 2.4–3.6 seconds.
- The zoomed 25 ms segment contains varying sample values and a visible
  negative offset.
- The RMS plot shows a large initial value that decreases, smaller energy
  bursts around 2.5–3.6 seconds, and a sharp peak around 6.7 seconds.

### Interpretation
The recording contains measurable signal variation. The cause of individual
waveform events has not yet been confirmed.

The negative offset and initial RMS behaviour warrant further investigation.
The current observations do not establish that the 32-bit I2S samples are
being converted into 16-bit PCM correctly.

### Validation status
- [x] Ten-second WAV generated.
- [x] Full waveform plotted.
- [x] 25 ms waveform segment plotted.
- [x] RMS energy plotted.
- [ ] Match waveform events to the recording actions.
- [ ] Listen to the WAV and assess intelligibility.
- [ ] Validate I2S sample alignment and PCM conversion.
- [ ] Confirm recording quality before proceeding to PRISM event detection.

### Next step
Correlate the waveform with the silence, clapping, and speech sequence, then
listen to the WAV. Do not modify the hardware configuration until these
checks are complete.

## INMP441 Audio Capture — Playback Validation

### Test performed
Captured a 10-second recording using the INMP441 connected to the ESP32.
The recording included silence, clapping, and speech.

### Playback results
- The generated `inmp441_recording.wav` plays correctly.
- Speech and clapping are clearly audible.
- The microphone is capturing useful acoustic information.
- The recorded audio was transferred from the ESP32 to the computer over
  serial and converted into a playable WAV file.

### Current status
- [x] Microphone responds to acoustic input.
- [x] I2S data capture operational.
- [x] Serial audio transfer operational.
- [x] WAV file generation operational.
- [x] Speech and clapping audible during playback.
- [x] Initial waveform and RMS plots generated.
- [ ] Investigate the initial waveform offset and unusual transient.
- [ ] Validate recording quality across repeated trials.
- [ ] Integrate audio capture into the PRISM inhaler pipeline.

### Conclusion
The basic microphone-to-computer audio capture pipeline is operational.
The recording is intelligible and suitable for proceeding with controlled
audio experiments. Further signal-quality validation remains pending.

## INMP441 — Ambient Inhalation Recording and Waveform Analysis

### Objective
Test whether the INMP441 microphone can capture inhalation sounds and inspect
the recorded audio using waveform and RMS visualizations.

### 1. Test Setup
- Microphone: INMP441 MEMS microphone
- Microcontroller: ESP32 DevKit V1
- Interface: I2S
- Sample rate: 16,000 Hz
- Recording duration: 10 seconds
- Channels: Mono
- Output format: WAV, 16-bit PCM
- Microphone position: Near the user during inhalation; not attached to
  the inhaler.

The existing hardware wiring and I2S configuration were retained.

### 2. Audio Capture Pipeline
The following pipeline was used:

INMP441 -> ESP32 I2S -> Serial -> Python -> WAV file

The ESP32 captured 32-bit I2S samples and transmitted the audio data over
serial at 921600 baud.

A Python script using `pyserial`, `numpy`, and `wave` received the samples
and generated `inmp441_recording.wav`.

The conversion used was:

    samples = np.frombuffer(audio, dtype="<i4")
    pcm = (samples >> 16).astype(np.int16)

The resulting WAV file was playable, and speech and clapping were audible
in an earlier microphone test.

Note: The correctness of the I2S sample alignment and PCM conversion has
not been independently verified.

### 3. Inhalation Recording
A 10-second recording was made while the user inhaled near the microphone.

The microphone was not mounted on the inhaler.

The recording was analyzed using Python to generate:
- Full audio waveform
- A zoomed 25 ms waveform segment
- RMS energy over time

### 4. Waveform Observations
- The waveform begins with a substantial negative amplitude offset.
- The signal gradually approaches zero during approximately the first
  2.5 seconds.
- Multiple bursts of activity appear around 2.8–4.3 seconds.
- Additional transients appear around 5.4 seconds, 7.5 seconds, and
  approximately 8.5–8.8 seconds.
- The RMS plot shows elevated initial energy that decreases substantially
  over the recording.
- Lower-amplitude variations and intermittent energy peaks are visible
  throughout the remainder of the recording.

### 5. Interpretation
The recording contains measurable signal variation during the test.

The initial amplitude ramp and negative offset have not been explained.
Individual transients have not been conclusively associated with specific
acoustic events.

Because the microphone was positioned near the user rather than mounted
on the inhaler, this experiment evaluates ambient inhalation recording,
not the final inhaler-mounted configuration.

The recording alone does not establish inhalation detection accuracy or
inhalation technique quality.

### 6. Current Progress

- [x] INMP441 connected to ESP32.
- [x] I2S communication tested.
- [x] Microphone response to acoustic input observed.
- [x] Serial audio transfer implemented.
- [x] Ten-second WAV capture implemented.
- [x] WAV playback verified as intelligible in an earlier audio test.
- [x] Waveform and RMS plots generated for the inhalation recording.
- [ ] Investigate the initial amplitude ramp and negative offset.
- [ ] Verify I2S sample alignment and PCM conversion.
- [ ] Record a controlled silence baseline.
- [ ] Record repeated inhalation trials under consistent conditions.
- [ ] Compare silence and inhalation recordings quantitatively.
- [ ] Test the microphone in its intended physical position on the inhaler.
- [ ] Integrate validated audio capture with the PRISM event-detection pipeline.

### 7. Next Steps
1. Record a controlled silence baseline without moving the microphone.
2. Record inhalation near the microphone at a consistent distance.
3. Repeat the recordings to assess consistency.
4. Compare RMS energy and waveform patterns between silence and inhalation.
5. Investigate the initial offset without changing the working hardware setup.
6. Proceed to inhaler-mounted testing after the ambient recording behaviour
   has been characterized.

### Milestone Summary
The basic INMP441-to-computer audio capture and visualization pipeline is
operational. Ambient inhalation recording has been attempted, but repeatable
inhalation detection and the final inhaler-mounted configuration remain
unvalidated.