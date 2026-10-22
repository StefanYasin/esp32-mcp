# Wiring

Everything here assumes a classic ESP32 DevKit v1 (WROOM-32, 30 pins) and a 4 channel relay module. The sketch works with any pin count; the tables match the defaults in `firmware/esp32_mcp/esp32_mcp.ino`.

Read the safety section before you connect a relay to anything that runs on mains power.

## Default pin map

| Function | GPIO | Direction | Notes |
| --- | --- | --- | --- |
| Relay 1 | 16 | Output | Active low on most modules |
| Relay 2 | 17 | Output | Active low |
| Relay 3 | 18 | Output | Active low |
| Relay 4 | 19 | Output | Active low |
| Soil moisture | 34 | Analog input | ADC1, input only pin |
| Light sensor | 35 | Analog input | ADC1, input only pin |
| Spare digital | 2 | Bidirectional | Onboard LED on many boards |
| Spare digital | 4, 5, 13, 14, 15, 21, 22, 23, 25, 26, 27, 32, 33 | Bidirectional | In the GPIO allow list |

## Relay module

| Module pin | ESP32 pin | Notes |
| --- | --- | --- |
| VCC | 5V (VIN) | The coil driver needs 5V, not 3.3V |
| GND | GND | Must share ground with the ESP32 |
| IN1 | GPIO 16 | Relay 1 |
| IN2 | GPIO 17 | Relay 2 |
| IN3 | GPIO 18 | Relay 3 |
| IN4 | GPIO 19 | Relay 4 |

Most cheap boards are active low: the sketch has `activeLow = true` in the relay table, and `esp32_set_relay(relay=1, state="on")` drives the pin low. If your module is active high, set `activeLow` to `false` for that relay rather than flipping the wiring.

```text
             +---------------------------+
   5V  ------| VCC                   IN1 |------ GPIO 16
   GND ------| GND                   IN2 |------ GPIO 17
             |                       IN3 |------ GPIO 18
             |                       IN4 |------ GPIO 19
             |  relay module (opto)      |
             +---------------------------+
                 |      |      |      |
              COM/NO  COM/NO COM/NO COM/NO
                 |      |      |      |
              load 1  load 2 load 3 load 4
```

## Analog sensors

| Sensor | Sensor pin | ESP32 pin | Notes |
| --- | --- | --- | --- |
| Soil moisture (capacitive) | VCC | 3.3V | Do not power a resistive probe continuously, it corrodes |
| Soil moisture | GND | GND | |
| Soil moisture | AOUT | GPIO 34 | ADC1 channel 6 |
| Light sensor (LDR module) | VCC | 3.3V | |
| Light sensor | GND | GND | |
| Light sensor | AO | GPIO 35 | ADC1 channel 7 |

Use ADC1 pins (GPIO 32 to 39) for analog input. ADC2 (GPIO 0, 2, 4, 12 to 15, 25 to 27) is shared with WiFi and is unreliable while the radio is on.

The sketch reports raw counts (0 to 4095), millivolts and a scaled value. Calibrate the scale and offset in `ANALOG_SENSORS` for your own sensor:

```cpp
AnalogSensorConfig ANALOG_SENSORS[] = {
    {34, "soil_moisture", "percent", 0.0244f, 0.0f},  // raw * 0.0244 gives 0 to 100
    {35, "light", "percent", 0.0244f, 0.0f},
};
```

## Pins to leave alone

| GPIO | Why |
| --- | --- |
| 6 to 11 | Connected to the SPI flash. Using them bricks the boot |
| 0 | Boot mode strapping pin, pulled up at reset |
| 12 | Strapping pin, sets flash voltage. Pulling it up at boot can damage the board |
| 15 | Strapping pin, controls boot log output |
| 34 to 39 | Input only, no output driver and no pull up or pull down |
| 1, 3 | UART0, used by the serial monitor and flashing |

The default allow list in the sketch is `{2, 4, 5, 12, 13, 14, 15, 21, 22, 23, 25, 26, 27, 32, 33, 34, 35}`. It includes 12 and 15 because they are useful after boot, and the firmware refuses to drive 34 and 35. Treat 12 with care: a relay module that pulls it high at boot can stop the board from starting.

## Power

- The ESP32 dev board is happiest on a 5V supply that can deliver at least 500 mA.
- Relay coils pull 60 to 80 mA each while energised. Four of them plus the ESP32 can exceed what a USB port gives you.
- Power the relay module from a separate 5V supply if the board resets when relays click, and tie the grounds together.
- Add a 100 nF capacitor across each coil if you see resets, or pick a module that already has flyback diodes.
- Never power a relay module from the 3.3V pin. The coils will not pull in reliably and the regulator will overheat.

## Safety

- Mains voltage can kill you. If you are not confident working with it, use a relay module with a certified enclosure or switch low voltage loads only.
- Switch the live conductor, not the neutral, and keep the mains side physically separated from the ESP32 side.
- Use an opto-isolated module and keep the isolation gap clean. Do not run mains tracks next to the 3.3V wiring.
- Add a fuse on the mains input and strain relief on every cable.
- The firmware boots with all relays off, but do not rely on software alone for anything dangerous. A hardware interlock or a normally open contactor is the safe default.

## Changing the pin map

All of it lives in the CONFIG block at the top of the sketch:

```cpp
RelayConfig RELAYS[] = {
    {16, "relay1", true},
    {17, "relay2", true},
};

AnalogSensorConfig ANALOG_SENSORS[] = {
    {34, "soil_moisture", "percent", 0.0244f, 0.0f},
};

const uint8_t GPIO_ALLOWED[] = {2, 4, 5, 21, 22, 23};
```

Rules I follow when editing it:

1. Keep relay names short and ASCII. They go straight into JSON.
2. Add every pin you want an agent to reach to `GPIO_ALLOWED`. Nothing outside it is readable or writable.
3. Keep input only pins (34 to 39) out of the relay table.
4. Recheck the strapping pins before you add them. A pin that is high at boot changes how the board starts.
5. After flashing, run `curl http://<board-ip>/health` and confirm the relay table matches your wiring.

## Checking the wiring without an agent

```bash
curl http://esp32-mcp.local/health
curl -X POST http://esp32-mcp.local/relay/1/on
curl -X POST http://esp32-mcp.local/relay/1/off
curl "http://esp32-mcp.local/gpio?pin=2&value=1"
curl "http://esp32-mcp.local/gpio?pin=2"
curl http://esp32-mcp.local/sensors
```

If `/health` answers but a relay does not click, check the module supply and the active level before you touch the sketch. If nothing answers, check that the board and your machine are on the same network and that the router is not isolating clients.
