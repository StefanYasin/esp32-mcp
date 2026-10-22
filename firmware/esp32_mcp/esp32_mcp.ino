/*
  esp32-mcp firmware

  A small JSON HTTP API that the esp32-mcp MCP server calls over your LAN.
  No libraries beyond the ESP32 Arduino core (WiFi, WebServer, ESPmDNS).

  Routes
    GET  /health                board info, uptime, relay table
    GET  /sensors               internal chip temperature + analog channels
    GET  /relay/{n}/on          switch relay n on   (POST works too)
    GET  /relay/{n}/off         switch relay n off  (POST works too)
    GET  /gpio?pin=N            read mode and digital level
    POST /gpio?pin=N&value=1    drive a pin high or low
    POST /gpio?pin=N&mode=output|input|input_pullup

  Everything is configured in the CONFIG block below. Pin names must be plain
  ASCII without quotes or backslashes because the JSON is assembled by hand.

  Board: classic ESP32 (DevKit v1, WROOM-32). See docs/wiring.md.
*/

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

// ============================== CONFIG ==============================

const char *WIFI_SSID = "YOUR_WIFI_SSID";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Hostname used for mDNS, so http://esp32-mcp.local resolves on the LAN.
const char *HOSTNAME = "esp32-mcp";

// Leave empty to disable auth. Set a shared secret to require the header
// X-Api-Token on every request, and give the same value to the MCP server
// through ESP32_MCP_TOKEN.
const char *API_TOKEN = "";

const char *FIRMWARE_VERSION = "0.1.0";

// WebServer only exposes headers that were registered up front, so the token
// header has to be collected or hasHeader() would always return false.
const char *COLLECTED_HEADERS[] = {"X-Api-Token"};
const size_t COLLECTED_HEADER_COUNT =
    sizeof(COLLECTED_HEADERS) / sizeof(COLLECTED_HEADERS[0]);

struct RelayConfig {
  uint8_t pin;
  const char *name;
  bool activeLow;  // most cheap relay boards pull the coil low to close
};

RelayConfig RELAYS[] = {
    {16, "relay1", true},
    {17, "relay2", true},
    {18, "relay3", true},
    {19, "relay4", true},
};

struct AnalogSensorConfig {
  uint8_t pin;
  const char *id;
  const char *unit;
  float scale;   // value = raw * scale + offset
  float offset;
};

AnalogSensorConfig ANALOG_SENSORS[] = {
    {34, "soil_moisture", "percent", 0.0244f, 0.0f},
    {35, "light", "percent", 0.0244f, 0.0f},
};

// Pins the /gpio endpoint may touch. Keep the flash pins (6 to 11) and the
// strapping pins you rely on (0, 2, 12, 15) out unless you know why.
const uint8_t GPIO_ALLOWED[] = {2, 4, 5, 12, 13, 14, 15, 21, 22, 23,
                                25, 26, 27, 32, 33, 34, 35};

// ============================ END CONFIG ============================

const uint8_t RELAY_COUNT = sizeof(RELAYS) / sizeof(RELAYS[0]);
const uint8_t ANALOG_SENSOR_COUNT =
    sizeof(ANALOG_SENSORS) / sizeof(ANALOG_SENSORS[0]);
const uint8_t GPIO_ALLOWED_COUNT = sizeof(GPIO_ALLOWED) / sizeof(GPIO_ALLOWED[0]);

const unsigned long WIFI_RETRY_MS = 5000;
const int HTTP_PORT = 80;

WebServer server(HTTP_PORT);

bool relayState[RELAY_COUNT];
String pinModes[40];
unsigned long bootMs = 0;
unsigned long lastWifiAttempt = 0;

// ------------------------------ helpers ------------------------------

String jsonEscape(const String &input) {
  String out;
  out.reserve(input.length() + 8);
  for (size_t i = 0; i < input.length(); i++) {
    char c = input[i];
    if (c == '"' || c == '\\') {
      out += '\\';
    }
    out += c;
  }
  return out;
}

void sendJson(int status, const String &body) {
  server.sendHeader("Cache-Control", "no-store");
  server.send(status, "application/json", body);
}

void sendError(int status, const String &message) {
  String body = "{\"ok\":false,\"error\":\"";
  body += jsonEscape(message);
  body += "\"}";
  sendJson(status, body);
}

bool authorized() {
  if (strlen(API_TOKEN) == 0) {
    return true;
  }
  if (server.hasHeader("X-Api-Token") &&
      server.header("X-Api-Token") == API_TOKEN) {
    return true;
  }
  sendError(401, "missing or invalid X-Api-Token");
  return false;
}

bool isAllowedGpio(int pin) {
  for (uint8_t i = 0; i < GPIO_ALLOWED_COUNT; i++) {
    if ((int)GPIO_ALLOWED[i] == pin) {
      return true;
    }
  }
  return false;
}

bool isInputOnly(int pin) { return pin >= 34 && pin <= 39; }

void applyRelay(uint8_t index, bool on) {
  bool level = RELAYS[index].activeLow ? !on : on;
  digitalWrite(RELAYS[index].pin, level ? HIGH : LOW);
  relayState[index] = on;
}

// ------------------------------ handlers ------------------------------

void handleHealth() {
  String body = "{\"ok\":true,\"device\":\"esp32-mcp\",\"firmware\":\"";
  body += FIRMWARE_VERSION;
  body += "\",\"hostname\":\"";
  body += jsonEscape(HOSTNAME);
  body += "\",\"ip\":\"";
  body += WiFi.localIP().toString();
  body += "\",\"uptime_ms\":";
  body += String(millis() - bootMs);
  body += ",\"free_heap\":";
  body += String(ESP.getFreeHeap());
  body += ",\"relays\":[";
  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    if (i > 0) {
      body += ",";
    }
    body += "{\"index\":";
    body += String((int)i + 1);
    body += ",\"name\":\"";
    body += jsonEscape(RELAYS[i].name);
    body += "\",\"pin\":";
    body += String(RELAYS[i].pin);
    body += ",\"state\":\"";
    body += relayState[i] ? "on" : "off";
    body += "\"}";
  }
  body += "]}";
  sendJson(200, body);
}

void handleSensors() {
  String body = "{\"ok\":true,\"sensors\":[";
  bool first = true;

#if defined(CONFIG_IDF_TARGET_ESP32)
  body += "{\"id\":\"chip_temp\",\"type\":\"internal\",\"pin\":null,\"value\":";
  body += String(temperatureRead(), 1);
  body += ",\"unit\":\"c\"}";
  first = false;
#endif

  for (uint8_t i = 0; i < ANALOG_SENSOR_COUNT; i++) {
    if (!first) {
      body += ",";
    }
    first = false;
    int raw = analogRead(ANALOG_SENSORS[i].pin);
    int millivolts = analogReadMilliVolts(ANALOG_SENSORS[i].pin);
    float value =
        (float)raw * ANALOG_SENSORS[i].scale + ANALOG_SENSORS[i].offset;
    body += "{\"id\":\"";
    body += jsonEscape(ANALOG_SENSORS[i].id);
    body += "\",\"type\":\"analog\",\"pin\":";
    body += String(ANALOG_SENSORS[i].pin);
    body += ",\"raw\":";
    body += String(raw);
    body += ",\"millivolts\":";
    body += String(millivolts);
    body += ",\"value\":";
    body += String(value, 1);
    body += ",\"unit\":\"";
    body += jsonEscape(ANALOG_SENSORS[i].unit);
    body += "\"}";
  }
  body += "]}";
  sendJson(200, body);
}

void sendGpioState(int pin) {
  String mode = pinModes[pin];
  if (mode.length() == 0) {
    mode = "input";
  }
  String body = "{\"ok\":true,\"pin\":";
  body += String(pin);
  body += ",\"mode\":\"";
  body += mode;
  body += "\",\"value\":";
  body += String(digitalRead(pin));
  body += "}";
  sendJson(200, body);
}

void handleGpio() {
  if (!authorized()) {
    return;
  }
  if (!server.hasArg("pin")) {
    sendError(400, "missing pin query parameter");
    return;
  }
  int pin = server.arg("pin").toInt();
  if (!isAllowedGpio(pin)) {
    sendError(403, "pin " + String(pin) + " is not in the allowed gpio list");
    return;
  }

  bool hasMode = server.hasArg("mode");
  bool hasValue = server.hasArg("value");

  if (!hasMode && !hasValue) {
    sendGpioState(pin);
    return;
  }

  if (server.method() != HTTP_POST) {
    sendError(405, "use POST to change a pin");
    return;
  }

  if (hasMode) {
    String mode = server.arg("mode");
    if (mode == "output") {
      if (isInputOnly(pin)) {
        sendError(400, "gpio " + String(pin) + " is input only");
        return;
      }
      pinMode(pin, OUTPUT);
    } else if (mode == "input") {
      pinMode(pin, INPUT);
    } else if (mode == "input_pullup") {
      pinMode(pin, INPUT_PULLUP);
    } else {
      sendError(400, "mode must be output, input or input_pullup");
      return;
    }
    pinModes[pin] = mode;
  }

  if (hasValue) {
    if (isInputOnly(pin)) {
      sendError(400, "gpio " + String(pin) + " is input only");
      return;
    }
    String raw = server.arg("value");
    if (raw != "0" && raw != "1") {
      sendError(400, "value must be 0 or 1");
      return;
    }
    if (pinModes[pin] != "output") {
      pinMode(pin, OUTPUT);
      pinModes[pin] = "output";
    }
    digitalWrite(pin, raw == "1" ? HIGH : LOW);
  }

  sendGpioState(pin);
}

bool digitsOnly(const String &value) {
  if (value.length() == 0) {
    return false;
  }
  for (size_t i = 0; i < value.length(); i++) {
    if (!isDigit(value[i])) {
      return false;
    }
  }
  return true;
}

// Handles /relay/<index>/<on|off>. Parsed here instead of with UriBraces
// because the ESP32 core dropped UriBraces, and this works on every version.
void handleRelayPath(const String &path) {
  if (!authorized()) {
    return;
  }
  if (server.method() != HTTP_GET && server.method() != HTTP_POST) {
    sendError(405, "use GET or POST");
    return;
  }

  int lastSlash = path.lastIndexOf('/');
  if (lastSlash < 8) {
    sendError(404, "no route for " + path);
    return;
  }
  String indexPart = path.substring(7, lastSlash);
  String action = path.substring(lastSlash + 1);

  if (action != "on" && action != "off") {
    sendError(404, "unknown relay action: " + action);
    return;
  }
  if (!digitsOnly(indexPart)) {
    sendError(400, "relay index must be a number");
    return;
  }

  long index = indexPart.toInt();
  if (index < 1 || index > (long)RELAY_COUNT) {
    sendError(404, "relay " + indexPart + " does not exist");
    return;
  }

  uint8_t slot = (uint8_t)(index - 1);
  bool on = (action == "on");
  applyRelay(slot, on);

  String body = "{\"ok\":true,\"relay\":";
  body += String(index);
  body += ",\"name\":\"";
  body += jsonEscape(RELAYS[slot].name);
  body += "\",\"pin\":";
  body += String(RELAYS[slot].pin);
  body += ",\"state\":\"";
  body += on ? "on" : "off";
  body += "\"}";
  sendJson(200, body);
}

void handleNotFound() {
  String path = server.uri();
  if (path.startsWith("/relay/")) {
    handleRelayPath(path);
    return;
  }
  sendError(404, "no route for " + path);
}

// ------------------------------ wifi ------------------------------

void connectWifi() {
  Serial.print("WiFi connecting to ");
  Serial.println(WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi failed, retrying from loop()");
  }
}

// ------------------------------ arduino ------------------------------

void setup() {
  Serial.begin(115200);
  delay(200);
  bootMs = millis();
  Serial.println();
  Serial.print("esp32-mcp firmware ");
  Serial.println(FIRMWARE_VERSION);

  for (uint8_t i = 0; i < 40; i++) {
    pinModes[i] = "";
  }

  for (uint8_t i = 0; i < RELAY_COUNT; i++) {
    pinMode(RELAYS[i].pin, OUTPUT);
    applyRelay(i, false);  // fail safe: all relays off at boot
    pinModes[RELAYS[i].pin] = "output";
  }

  for (uint8_t i = 0; i < ANALOG_SENSOR_COUNT; i++) {
    pinMode(ANALOG_SENSORS[i].pin, INPUT);
  }
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  connectWifi();

  if (MDNS.begin(HOSTNAME)) {
    MDNS.addService("http", "tcp", HTTP_PORT);
    Serial.print("mDNS: http://");
    Serial.print(HOSTNAME);
    Serial.println(".local/");
  }

  server.on("/health", HTTP_GET, handleHealth);
  server.on("/sensors", HTTP_GET, handleSensors);
  server.on("/gpio", HTTP_GET, handleGpio);
  server.on("/gpio", HTTP_POST, handleGpio);
  // Relay routes are matched in handleNotFound so the sketch does not depend
  // on UriBraces, which is gone from recent ESP32 cores.
  server.onNotFound(handleNotFound);
  server.collectHeaders(COLLECTED_HEADERS, COLLECTED_HEADER_COUNT);
  server.begin();

  Serial.print("HTTP API listening on port ");
  Serial.println(HTTP_PORT);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED &&
      millis() - lastWifiAttempt > WIFI_RETRY_MS) {
    lastWifiAttempt = millis();
    Serial.println("WiFi dropped, reconnecting");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
  server.handleClient();
  delay(2);
}
