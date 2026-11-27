/*
  emergency_monitor.ino - IoT sensor node for the AI-Powered Smart Medical Emergency System.

  Target board : Arduino Uno / Nano (ATmega328P, arduino:avr)   - also builds for Mega 2560
  Link         : USB serial, 115200 baud, JSON Lines (one complete JSON object per line, '\n' terminated)
  Receiver     : backend/patient_monitoring/bridge (python manage.py run_arduino_bridge)

  MODES
    SIMULATION_MODE 1  -> generates demonstration values, every line says "source":"SIMULATED".
    SIMULATION_MODE 0  -> reads real sensors only. No sensor is wired yet, so every sensor reports
                          "status":"NOT_CONNECTED" and NO value is ever invented.
  Simulation is never switched on automatically: it is a compile-time choice made below.

  LINES SENT
    {"type":"hello","device_id":"ARDUINO_DEVICE_01","fw":"1.0.0","boot_id":"3FA2C1D0","mode":"SIMULATED","interval_ms":5000}
    {"type":"reading","device_id":"ARDUINO_DEVICE_01","boot_id":"3FA2C1D0","seq":7,"uptime_ms":35012,
     "sensor_type":"temperature","value":36.8,"unit":"C","source":"SIMULATED","status":"VALID"}
    {"type":"heartbeat",...}   every interval, so the backend knows the board is still alive
    {"type":"ack"|"error","message":"..."}   replies to commands

  The board has no real-time clock: it sends uptime_ms + a per-boot sequence number. The bridge adds the
  wall-clock timestamp when the line arrives; (device_id, boot_id, seq) makes every reading unique.

  COMMANDS (type in the Serial Monitor, "Newline" line ending)
    INTERVAL <ms>            sampling interval, 1000..60000
    SCENARIO NORMAL|FEVER|HYPOXIA|TACHYCARDIA   (simulation only)
    STATUS                   re-send the hello line
*/

// ------------------------------------------------------------------ configuration
#define SIMULATION_MODE 1                     // 1 = SIMULATED demo values, 0 = real sensors only
const char DEVICE_ID[] = "ARDUINO_DEVICE_01"; // must be registered in Django admin (IoT devices)
const char FW_VERSION[] = "1.0.0";
const unsigned long BAUD_RATE = 115200;
unsigned long sampleIntervalMs = 5000;

// ------------------------------------------------------------------ sensor table
enum SensorStatus { VALID, INVALID, SENSOR_ERROR, NOT_CONNECTED };
const char *const STATUS_NAMES[] = {"VALID", "INVALID", "SENSOR_ERROR", "NOT_CONNECTED"};

struct Sensor {
  const char *type;
  const char *unit;
  float minValid, maxValid;  // plausibility range, same as the backend check
  uint8_t decimals;
  float simBase;             // simulation: current baseline (changed by SCENARIO)
  float simValue;            // simulation: random-walk state
  float simJitter;
};

Sensor sensors[] = {
  {"temperature", "C",   30.0, 43.0, 1, 36.8, 36.8, 0.05},
  {"heart_rate",  "bpm", 30.0, 220.0, 0, 76.0, 76.0, 1.5},
  {"spo2",        "%",   70.0, 100.0, 0, 98.0, 98.0, 0.4},
};
const uint8_t SENSOR_COUNT = sizeof(sensors) / sizeof(sensors[0]);

char bootId[9];
unsigned long seq = 0;
unsigned long lastSample = 0;
char cmdBuf[40];
uint8_t cmdLen = 0;

// ------------------------------------------------------------------ JSON helpers (no library needed)
void jsonKey(const char *key, bool first = false) {
  if (!first) Serial.print(',');
  Serial.print('"'); Serial.print(key); Serial.print(F("\":"));
}
void jsonStr(const char *key, const char *val, bool first = false) {
  jsonKey(key, first); Serial.print('"'); Serial.print(val); Serial.print('"');
}
void jsonHeader(const char *type) {
  Serial.print('{');
  jsonStr("type", type, true);
  jsonStr("device_id", DEVICE_ID);
  jsonStr("boot_id", bootId);
}
const char *modeName() { return SIMULATION_MODE ? "SIMULATED" : "REAL"; }

void sendHello() {
  jsonHeader("hello");
  jsonStr("fw", FW_VERSION);
  jsonStr("mode", modeName());
  jsonKey("interval_ms"); Serial.print(sampleIntervalMs);
  jsonKey("sensors"); Serial.print('[');
  for (uint8_t i = 0; i < SENSOR_COUNT; i++) {
    if (i) Serial.print(',');
    Serial.print('"'); Serial.print(sensors[i].type); Serial.print('"');
  }
  Serial.println(F("]}"));
}

void sendHeartbeat() {
  jsonHeader("heartbeat");
  jsonStr("mode", modeName());
  jsonKey("uptime_ms"); Serial.print(millis());
  Serial.println('}');
}

void sendReply(const char *type, const __FlashStringHelper *msg) {
  Serial.print('{');
  jsonStr("type", type, true);
  jsonStr("device_id", DEVICE_ID);
  jsonKey("message"); Serial.print('"'); Serial.print(msg); Serial.println(F("\"}"));
}

void sendReading(const Sensor &s, SensorStatus st, float value) {
  jsonHeader("reading");
  jsonKey("seq"); Serial.print(++seq);
  jsonKey("uptime_ms"); Serial.print(millis());
  jsonStr("sensor_type", s.type);
  jsonKey("value");
  if (st == VALID) Serial.print(value, s.decimals); else Serial.print(F("null"));  // never a fake number
  jsonStr("unit", s.unit);
  jsonStr("source", modeName());
  jsonStr("status", STATUS_NAMES[st]);
  Serial.println('}');
}

// ------------------------------------------------------------------ sensor reading
// Real hardware: replace the body of readRealSensor() for a sensor once it is wired, e.g. a DS18B20 on a
// digital pin or a MAX30102 on I2C (A4/A5). Until then it honestly reports NOT_CONNECTED.
SensorStatus readRealSensor(const Sensor &s, float &value) {
  (void)s;
  value = NAN;
  return NOT_CONNECTED;
}

#if SIMULATION_MODE
SensorStatus readSimulatedSensor(Sensor &s, float &value) {
  float step = (random(-1000, 1001) / 1000.0) * s.simJitter;
  s.simValue += step + (s.simBase - s.simValue) * 0.2;  // drift back toward the baseline
  value = s.simValue;
  if (strcmp(s.type, "spo2") == 0 && value > 100) value = 100;
  return VALID;
}
#endif

SensorStatus readSensor(Sensor &s, float &value) {
#if SIMULATION_MODE
  SensorStatus st = readSimulatedSensor(s, value);
#else
  SensorStatus st = readRealSensor(s, value);
#endif
  if (st == VALID && (isnan(value) || value < s.minValid || value > s.maxValid)) st = INVALID;  // validation
  return st;
}

void sampleAll() {
  for (uint8_t i = 0; i < SENSOR_COUNT; i++) {
    float v;
    SensorStatus st = readSensor(sensors[i], v);
    sendReading(sensors[i], st, v);
  }
  sendHeartbeat();
}

// ------------------------------------------------------------------ commands from the computer
void setScenario(float t, float hr, float sp) {
  sensors[0].simBase = t; sensors[1].simBase = hr; sensors[2].simBase = sp;
}

void handleCommand(char *cmd) {
  for (char *p = cmd; *p; p++) *p = toupper(*p);
  if (strncmp(cmd, "INTERVAL ", 9) == 0) {
    long ms = atol(cmd + 9);
    if (ms >= 1000 && ms <= 60000) { sampleIntervalMs = ms; sendReply("ack", F("interval changed")); }
    else sendReply("error", F("interval must be 1000..60000 ms"));
  } else if (strcmp(cmd, "STATUS") == 0) {
    sendHello();
  } else if (strncmp(cmd, "SCENARIO ", 9) == 0) {
#if SIMULATION_MODE
    const char *n = cmd + 9;
    if (strcmp(n, "NORMAL") == 0) setScenario(36.8, 76, 98);
    else if (strcmp(n, "FEVER") == 0) setScenario(39.2, 108, 96);
    else if (strcmp(n, "HYPOXIA") == 0) setScenario(37.1, 118, 86);
    else if (strcmp(n, "TACHYCARDIA") == 0) setScenario(37.0, 145, 95);
    else { sendReply("error", F("unknown scenario")); return; }
    sendReply("ack", F("scenario changed"));
#else
    sendReply("error", F("scenarios exist only in SIMULATION_MODE"));
#endif
  } else if (cmd[0]) {
    sendReply("error", F("unknown command"));
  }
}

void readCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      cmdBuf[cmdLen] = '\0';
      if (cmdLen) handleCommand(cmdBuf);
      cmdLen = 0;
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    }
  }
}

// ------------------------------------------------------------------ main
void setup() {
  Serial.begin(BAUD_RATE);
  while (!Serial) { }  // boards with native USB wait for the port; the Uno continues at once
  randomSeed(analogRead(A0) ^ (analogRead(A1) << 4) ^ micros());  // floating pins = noise
  snprintf(bootId, sizeof(bootId), "%04X%04X", (unsigned)random(0, 0xFFFF), (unsigned)random(0, 0xFFFF));
  sendHello();
  lastSample = millis();
}

void loop() {
  readCommands();
  if (millis() - lastSample >= sampleIntervalMs) {  // overflow-safe comparison
    lastSample += sampleIntervalMs;
    sampleAll();
  }
}
