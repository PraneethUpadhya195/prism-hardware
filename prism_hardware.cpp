#include <ESP_I2S.h>

I2SClass I2S;

// Audio configuration
constexpr int SAMPLE_RATE = 8000;       // 8 kHz
constexpr int RECORD_SECONDS = 10;      // 10-second recording
constexpr int SAMPLES_TO_RECORD =
    SAMPLE_RATE * RECORD_SECONDS;       // 80,000 samples

// Serial configuration
constexpr uint32_t SERIAL_BAUD = 921600;

// Transfer buffer: 256 samples × 4 bytes = 1024 bytes
int32_t samples[256];

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1500);

  // INMP441 wiring:
  // SCK/BCLK -> GPIO 26
  // WS/LRCL  -> GPIO 25
  // SD       -> GPIO 33
  // L/R      -> GND (left channel)

  // setPins(BCLK, WS, DOUT, DIN, MCLK)
  I2S.setPins(26, 25, -1, 33, -1);

  // Configure standard I2S / Philips protocol,
  // 32-bit data width, mono left channel, 8 kHz.
  if (!I2S.begin(
        I2S_MODE_STD,
        SAMPLE_RATE,
        I2S_DATA_BIT_WIDTH_32BIT,
        I2S_SLOT_MODE_MONO,
        I2S_STD_SLOT_LEFT)) {
    Serial.println("I2S_INIT_FAILED");
    return;
  }

  Serial.println("I2S_READY");
  delay(1000);

  // Marker identifying the start of binary audio.
  Serial.println("AUDIO_BEGIN");

  int captured = 0;

  while (captured < SAMPLES_TO_RECORD) {
    int count = min(
        256,
        SAMPLES_TO_RECORD - captured
    );

    // Read 32-bit samples from I2S.
    size_t bytesRead = I2S.readBytes(
        reinterpret_cast<char *>(samples),
        count * sizeof(int32_t)
    );

    int samplesRead = bytesRead / sizeof(int32_t);

    if (samplesRead <= 0) {
      break;
    }

    // Transmit raw sample bytes without text formatting.
    Serial.write(
        reinterpret_cast<const uint8_t *>(samples),
        samplesRead * sizeof(int32_t)
    );

    captured += samplesRead;
  }

  Serial.flush();
}

void loop() {
  delay(1000);
}