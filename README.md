# esp32-mcp

An MCP server that gives an AI agent control of an ESP32: relays, GPIO pins and analog sensors. The Python side has no dependencies and implements the MCP stdio protocol by hand. The board runs a single Arduino sketch and speaks JSON over HTTP.

```text
AI agent  <-- stdio, JSON-RPC 2.0 -->  esp32-mcp  <-- HTTP, JSON -->  ESP32 firmware
```

I built this because most "AI controls hardware" demos hide a cloud service in the middle. Here the agent talks to a process on your machine, that process talks to a board on your LAN, and nothing leaves the network.

## Status

v0.1.0. The whole path works end to end against a simulated board: handshake, tool discovery, relay switching, sensor reads and GPIO reads and writes. The test suite runs without hardware. The firmware is written for the classic ESP32 and has not been compiled in CI yet, see Deferred.

## What is in the box

| Path | What it is |
| --- | --- |
| `src/esp32_mcp/` | MCP server, JSON-RPC framing, HTTP device client, tool registry |
| `src/esp32_mcp/fake_device.py` | Simulated ESP32 used by the tests and the demo |
| `firmware/esp32_mcp/esp32_mcp.ino` | Complete Arduino sketch, pin map at the top |
| `scripts/demo.py` | Spawns the server, runs the handshake and calls real tools |
| `docs/wiring.md` | Pin tables, wiring diagram, power and safety notes |
| `tests/` | pytest suite, no hardware required |

## Install

```bash
git clone https://github.com/StefanYasin/esp32-mcp.git
cd esp32-mcp
python -m pip install -e .
```

Python 3.9 or newer. There are no runtime dependencies.

## Try it without hardware

```bash
python scripts/demo.py --fake
```

That starts the built in simulator, spawns `esp32-mcp` as a real subprocess and prints the full JSON-RPC exchange: `initialize`, `tools/list`, then calls to `esp32_health`, `esp32_read_sensors`, `esp32_set_relay`, `esp32_gpio_write` and `esp32_gpio_read`.

## Flash the firmware

1. Install the ESP32 board package in the Arduino IDE (`Boards Manager` -> `esp32 by Espressif`).
2. Open `firmware/esp32_mcp/esp32_mcp.ino`.
3. Edit the CONFIG block at the top: WiFi credentials, `API_TOKEN`, the relay table, the analog sensor table and the GPIO allow list.
4. Select `ESP32 Dev Module`, upload, then open the serial monitor at 115200 baud.
5. Note the IP address it prints, or use the mDNS name `http://esp32-mcp.local`.

```bash
curl http://esp32-mcp.local/health
curl -X POST http://esp32-mcp.local/relay/1/on
curl "http://esp32-mcp.local/gpio?pin=2&value=1"
```

Wiring, pin tables and the safety notes are in [docs/wiring.md](docs/wiring.md).

## Connect an MCP client

Point the client at the `esp32-mcp` executable and give it the board URL. This is the Claude Desktop shape; other clients use the same command and env fields.

```json
{
  "mcpServers": {
    "esp32": {
      "command": "esp32-mcp",
      "env": {
        "ESP32_MCP_URL": "http://192.168.1.42",
        "ESP32_MCP_TOKEN": ""
      }
    }
  }
}
```

If you installed into a virtualenv, use the absolute path to the `esp32-mcp` executable in that venv.

Environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ESP32_MCP_URL` | `http://esp32-mcp.local` | Base URL of the board |
| `ESP32_MCP_TOKEN` | empty | Sent as `X-Api-Token` when set |
| `ESP32_MCP_TIMEOUT` | `5.0` | HTTP timeout in seconds |

Flags `--url`, `--token`, `--timeout` and `--verbose` override the environment. Logs go to stderr so they never corrupt the protocol stream.

## Tools

| Tool | Arguments | What it does |
| --- | --- | --- |
| `esp32_health` | none | Firmware version, IP, uptime, free heap and the relay table |
| `esp32_read_sensors` | none | Chip temperature plus every configured analog channel |
| `esp32_set_relay` | `relay` (1 to 8), `state` (`on` or `off`) | Switches one relay, idempotent |
| `esp32_gpio_read` | `pin` (0 to 39) | Reads pin mode and digital level |
| `esp32_gpio_write` | `pin`, optional `value` (0 or 1), optional `mode` (`output`, `input`, `input_pullup`) | Changes a pin mode and optionally drives it |

Agents call `esp32_health` first to learn which relays and pins exist. Bad arguments come back as a tool result with `isError: true` and a message that says what to fix. An unknown tool name is a JSON-RPC `-32602` error, which is what the MCP spec asks for.

Only pins in the firmware allow list can be touched. The default list leaves out the flash pins and the strapping pins.

## HTTP API on the board

The server and the firmware share this contract. It is small on purpose so the sketch stays readable.

| Method | Path | Response |
| --- | --- | --- |
| GET | `/health` | `{"ok":true,"device":"esp32-mcp","firmware":"0.1.0",...,"relays":[{"index":1,"name":"relay1","pin":16,"state":"off"}]}` |
| GET | `/sensors` | `{"ok":true,"sensors":[{"id":"chip_temp","value":41.5,"unit":"c"},{"id":"soil_moisture","pin":34,"raw":1820,"millivolts":1466,"value":44.4,"unit":"percent"}]}` |
| GET or POST | `/relay/{n}/on` | `{"ok":true,"relay":1,"name":"relay1","pin":16,"state":"on"}` |
| GET or POST | `/relay/{n}/off` | `{"ok":true,"relay":1,"name":"relay1","pin":16,"state":"off"}` |
| GET | `/gpio?pin=N` | `{"ok":true,"pin":2,"mode":"output","value":1}` |
| POST | `/gpio?pin=N&value=0\|1` | Same shape, sets the level |
| POST | `/gpio?pin=N&mode=output\|input\|input_pullup` | Same shape, sets the mode |

Errors use the HTTP status plus `{"ok":false,"error":"..."}`: 400 for bad parameters, 401 for a bad token, 403 for a pin outside the allow list, 404 for an unknown relay or route, 405 for the wrong method.

## Protocol details

The MCP stdio transport is newline delimited JSON-RPC 2.0, one message per line, UTF-8. This server implements:

- `initialize` with protocol version `2024-11-05`, `tools` capability and server instructions
- `notifications/initialized`
- `ping`
- `tools/list`
- `tools/call`

Anything else returns `-32601 Method not found`. Malformed JSON returns `-32700` and the stream stays open. Notifications never get a response.

## Tests

```bash
python -m pytest
```

88 tests covering the JSON-RPC layer, tool schemas and dispatch, the HTTP client, the simulator contract and the real subprocess stdio transport. No hardware and no network access required; the simulator binds to `127.0.0.1` on a random port.

## Security

The board API is plain HTTP with an optional shared token. That is fine on a trusted LAN and wrong on the public internet. Do not port forward the ESP32, and do not put this behind a public reverse proxy without TLS and real authentication.

Relays can switch mains voltage. Use an opto-isolated relay module, keep the mains side away from the logic side, and read [docs/wiring.md](docs/wiring.md) before connecting anything.

## Deferred

- TLS and per client credentials
- PWM and analog writes, servo control
- Sensor drivers (DHT22, DS18B20, I2C) beyond the internal temperature and raw ADC channels
- OTA firmware updates
- A compile job for the sketch in CI, since the ESP32 core is a large download
- Newer MCP protocol revisions, the server pins `2024-11-05` for now

## License

MIT. See [LICENSE](LICENSE).

Built by Stefan Yasin (https://stefanyasin.github.io/)
