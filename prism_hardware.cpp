/*
  PRISM INMP441 capture firmware — ESP32 DevKit V1 + INMP441 (L/R -> GND)
  Arduino-ESP32 core 3.x (ESP_I2S).  Host script: record_audio.py (protocol v2).

  What changed from v1 and why (see inmp441_dry_run REPORT, 2026-10-19):
  - Samples are sent exactly as received (raw 32-bit I2S words, no shift, no gain).
    The host checks bit alignment and overload from them and converts to 16-bit with
    saturation, so no wrap-around can be introduced on this side.
  - 4 s warm-up is read and discarded after power-up (the v1 files carried a
    -2,200..-3,700 count offset ramp for ~2.5 s after a 1 s delay).  Between
    recordings the stream keeps running and is discarded, so the microphone stays
    clocked and settled.
  - The host triggers each recording ('R'), so several takes need no reset, and the
    sample rate / length are announced once (PRISM_CFG) instead of being duplicated
    in the host script.
  - Each recording ends with a CRC-32 of the payload and I2S timing stamps, so the
    host can detect lost serial bytes and measure the real sample rate.
  - 2.1.0: receive timing corrected (cfg::RX_BIT_SHIFT).  With the default Philips
    timing this board captured every sample one bit late, losing the sign bit, which
    is what made loud breath wrap around in the v1 recordings.  Verified on hardware:
    alignment, rate (7,992.9 Hz), CRC and start-up checks all pass.

  Protocol (text lines end with \r\n; anything else, e.g. boot ROM output, is ignored):
    boot          -> I2S_READY, (warm-up), PRISM_CFG key=value..., READY
    host "I\n"    -> PRISM_CFG ..., READY             (for boards that do not auto-reset)
    host "R\n"    -> AUDIO_BEGIN
                     <SAMPLES x int32, little-endian, raw I2S words>
                     AUDIO_END samples=.. crc32=.. n_mark=.. t_mark_us=.. n_last=.. t_last_us=.. min=.. max=..
                     READY
    faults        -> I2S_INIT_FAILED | I2S_DISCARD_FAILED (repeated every second)
                     I2S_READ_FAILED captured=N   (mid-recording; the host discards the take)
*/

#include <Arduino.h>
#include <ESP_I2S.h>
#include "driver/i2s_std.h"

namespace cfg {
constexpr char FW_VERSION[] = "2.1.0";
constexpr uint32_t SAMPLE_RATE = 8000;                 // Hz; PRISM's detector requires exactly 8 kHz
constexpr uint32_t RECORD_SECONDS = 10;
constexpr uint32_t SAMPLES = SAMPLE_RATE * RECORD_SECONDS;
constexpr uint32_t WARMUP_MS = 4000;                   // discarded once after power-up
constexpr uint32_t SERIAL_BAUD = 921600;               // 32 kB/s needed, ~92 kB/s available
constexpr int PIN_BCLK = 26;                           // INMP441 SCK
constexpr int PIN_WS = 25;                             // INMP441 WS
constexpr int PIN_DIN = 33;                            // INMP441 SD
constexpr size_t BLOCK = 256;                          // samples per read: 32 ms at 8 kHz
constexpr uint32_t TIMING_MARK_SAMPLES = SAMPLE_RATE;  // rate is measured from 1 s onward
constexpr uint32_t I2S_TIMEOUT_MS = 1000;
// Philips I2S puts the MSB one SCK after the WS edge, and ESP_I2S configures the receiver for that
// (bit_shift = true).  Measured on this ESP32 DevKit V1 + INMP441 (2026-10-19, fw 2.0.0): bits 8..0 of
// every word were 0 and bit 9 carried the INMP441's noise LSB, i.e. the receiver started one bit late
// and dropped the sign bit, so anything above half scale wrapped around.  Sampling without the extra
// delay puts the 24-bit sample in bits 31..8.  record_audio.py verifies this on every take.
constexpr bool RX_BIT_SHIFT = false;
}  // namespace cfg

I2SClass I2S;
static int32_t block[cfg::BLOCK];
static const char *fault = nullptr;

// CRC-32 (IEEE 802.3, reflected, as Python's zlib.crc32); chainable like zlib.crc32(data, crc).
static uint32_t crc32Update(uint32_t crc, const uint8_t *data, size_t len) {
  crc = ~crc;
  while (len--) {
    crc ^= *data++;
    for (int k = 0; k < 8; k++) {
      crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
  }
  return ~crc;
}

// ESP_I2S::readBytes returns either the full request or 0 (error/timeout).
static bool readBlock() {
  return I2S.readBytes(reinterpret_cast<char *>(block), sizeof(block)) == sizeof(block);
}

// Re-apply the RX slot with the chosen bit_shift (ESP_I2S::begin has no parameter for it).
static bool configureRxSlot() {
  i2s_chan_handle_t rx = I2S.rxChan();
  if (rx == nullptr) {
    return false;
  }
  i2s_std_slot_config_t slot = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO);
  slot.slot_mask = I2S_STD_SLOT_LEFT;
  slot.bit_shift = cfg::RX_BIT_SHIFT;
  return i2s_channel_disable(rx) == ESP_OK && i2s_channel_reconfig_std_slot(rx, &slot) == ESP_OK
         && i2s_channel_enable(rx) == ESP_OK;
}

static void announce() {
  Serial.printf("PRISM_CFG fw=%s sample_rate=%lu samples=%lu bytes_per_sample=4 warmup_ms=%lu "
                "format=i2s_std_32bit_mono_left rx_bit_shift=%d word=raw_int32_le pins=bclk%d_ws%d_sd%d\r\n",
                cfg::FW_VERSION, (unsigned long)cfg::SAMPLE_RATE, (unsigned long)cfg::SAMPLES,
                (unsigned long)cfg::WARMUP_MS, cfg::RX_BIT_SHIFT ? 1 : 0, cfg::PIN_BCLK, cfg::PIN_WS, cfg::PIN_DIN);
  Serial.print("READY\r\n");
}

static void record() {
  uint32_t crc = 0, sent = 0, readTotal = 0;
  uint32_t nMark = 0, tMark = 0, tLast = 0;
  int32_t mn = INT32_MAX, mx = INT32_MIN;

  Serial.print("AUDIO_BEGIN\r\n");
  while (sent < cfg::SAMPLES) {
    if (!readBlock()) {
      Serial.printf("\r\nI2S_READ_FAILED captured=%lu\r\n", (unsigned long)sent);
      return;
    }
    tLast = micros();
    readTotal += cfg::BLOCK;
    if (nMark == 0 && readTotal >= cfg::TIMING_MARK_SAMPLES) {
      nMark = readTotal;
      tMark = tLast;
    }
    const size_t n = min<uint32_t>(cfg::BLOCK, cfg::SAMPLES - sent);
    for (size_t i = 0; i < n; i++) {
      mn = min(mn, block[i]);
      mx = max(mx, block[i]);
    }
    const uint8_t *bytes = reinterpret_cast<const uint8_t *>(block);
    Serial.write(bytes, n * sizeof(int32_t));
    crc = crc32Update(crc, bytes, n * sizeof(int32_t));
    sent += n;
  }
  Serial.flush();
  Serial.printf("AUDIO_END samples=%lu crc32=%08lx n_mark=%lu t_mark_us=%lu n_last=%lu t_last_us=%lu min=%ld max=%ld\r\n",
                (unsigned long)sent, (unsigned long)crc, (unsigned long)nMark, (unsigned long)tMark,
                (unsigned long)readTotal, (unsigned long)tLast, (long)mn, (long)mx);
}

void setup() {
  Serial.begin(cfg::SERIAL_BAUD);
  delay(300);

  // setPins(BCLK, WS, DOUT, DIN, MCLK)
  I2S.setPins(cfg::PIN_BCLK, cfg::PIN_WS, -1, cfg::PIN_DIN, -1);
  I2S.setTimeout(cfg::I2S_TIMEOUT_MS);
  // Standard I2S, 32-bit slots, mono, left slot (INMP441 L/R tied to GND); receive timing per
  // cfg::RX_BIT_SHIFT so that the INMP441's 24-bit sample arrives in bits 31..8 of each word.
  if (!I2S.begin(I2S_MODE_STD, cfg::SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT)
      || !configureRxSlot()) {
    fault = "I2S_INIT_FAILED";
    return;
  }
  Serial.print("I2S_READY\r\n");

  const uint32_t warmupBlocks = (cfg::SAMPLE_RATE * cfg::WARMUP_MS / 1000 + cfg::BLOCK - 1) / cfg::BLOCK;
  for (uint32_t i = 0; i < warmupBlocks; i++) {
    if (!readBlock()) {
      fault = "I2S_DISCARD_FAILED";
      return;
    }
  }
  announce();
}

void loop() {
  if (fault != nullptr) {
    Serial.printf("%s\r\n", fault);
    delay(1000);
    return;
  }
  while (Serial.available() > 0) {
    const int c = Serial.read();
    if (c == 'R') {
      record();
      Serial.print("READY\r\n");
    } else if (c == 'I') {
      announce();
    }
  }
  // Keep the microphone clocked and the DMA buffer drained between recordings.
  readBlock();
}
