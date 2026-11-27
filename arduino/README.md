# Arduino IoT monitoring

```
Arduino (emergency_monitor.ino)  or  virtual Arduino (tools/virtual_arduino.py)
        |  JSON Lines, 115200 baud  (USB COM port  /  socket://127.0.0.1:7000)
        v
python manage.py run_arduino_bridge        <- separate process; Django runs fine without it
        |  POST /api/iot/ingest/  (header X-Bridge-Token = ARDUINO_BRIDGE_TOKEN in backend/.env)
        v
patient_monitoring/iot_ingest.py  -> IoTDevice / SensorReading tables -> WebSocket nudge (/ws/notify/)
        |                                                   |
Operator dashboard "IoT devices (Arduino)" panel     Patient app: Results > IoT sensors
```

**SIMULATED readings are demo data and are labelled SIMULATED everywhere. They are never real measurements.**

## Files

| Path | What it does |
|---|---|
| `arduino/emergency_monitor/emergency_monitor.ino` | Firmware (Uno/Nano/Mega). `SIMULATION_MODE 1` = demo values, `0` = real sensors only |
| `arduino/tools/virtual_arduino.py` | Stands in for the board when none is connected (same protocol) |
| `backend/patient_monitoring/bridge/serial_parser.py` | Parses and checks one serial line |
| `backend/patient_monitoring/bridge/serial_bridge.py` | Serial reading, reconnects, spool when Django is down, lock file |
| `backend/patient_monitoring/management/commands/run_arduino_bridge.py` | start / stop / status / command |
| `backend/patient_monitoring/iot_ingest.py` | Django-side validation, storage, broadcasting |
| `backend/api/iot_api.py` | `/api/iot/ingest/`, `/api/iot/connection/`, `/api/iot/my-readings/` |
| `backend/templates/dashboard/_iot.html`, `backend/static/js/iot.js` | Operator dashboard panel |

## Simulation without a board (run in 3 PowerShell windows)

```powershell
# Window 1 - Django
cd F:\kaggle\AI_Medical_Emergency_System\backend
.\venv\Scripts\python.exe manage.py runserver

# Window 2 - virtual Arduino
cd F:\kaggle\AI_Medical_Emergency_System
.\backend\venv\Scripts\python.exe arduino\tools\virtual_arduino.py --interval 5

# Window 3 - bridge
cd F:\kaggle\AI_Medical_Emergency_System\backend
.\venv\Scripts\python.exe manage.py run_arduino_bridge --port socket://127.0.0.1:7000
```

Open http://127.0.0.1:8000/dashboard/ as an operator. The **IoT devices (Arduino)** panel updates by itself.

The first time a new `device_id` connects, it shows as **PENDING APPROVAL**. Approve it at `/admin/` > IoT devices (Arduino): tick *approved* and set *mode* to `SIMULATED`. `ARDUINO_DEVICE_01` is already approved.

## Managing the bridge (in another window)

```powershell
.\venv\Scripts\python.exe manage.py run_arduino_bridge --status
.\venv\Scripts\python.exe manage.py run_arduino_bridge --stop
.\venv\Scripts\python.exe manage.py run_arduino_bridge --command "SCENARIO HYPOXIA"   # NORMAL FEVER HYPOXIA TACHYCARDIA
.\venv\Scripts\python.exe manage.py run_arduino_bridge --list-ports
```

The log is in `backend\var\arduino_bridge\bridge.log`. Lines Django refused go to `rejected.jsonl`. Readings waiting for Django go to `spool.jsonl` and are re-sent automatically.

You can set the defaults in `backend\.env`: `ARDUINO_PORT=COM3` (or `auto`), `ARDUINO_BAUD=115200`, `ARDUINO_BACKEND_URL`.

## Testing failure handling

- `virtual_arduino.py --glitch 0.1` sends corrupted lines. The bridge logs *Malformed line ignored*.
- `virtual_arduino.py --drop-after 20` simulates unplugging the cable. The bridge reconnects.
- Stop Django while the bridge runs. Readings are spooled and delivered once Django is back. Duplicates are ignored.

## Showing readings to a patient

Readings are linked to a patient **only** when an administrator sets *patient* on the device in admin. They then appear in the mobile app under **Results > IoT sensors** for that patient only.

## With a real board later

1. Plug it in with a data USB cable. Run `--list-ports` to find its COM port.
2. Arduino IDE: open `emergency_monitor.ino`, then pick **Tools > Board** (e.g. Arduino Uno), **Tools > Port** (COMx), and click **Upload**.
3. **Tools > Serial Monitor** at **115200** baud should show JSON lines. Close the Serial Monitor afterwards, because only one program can use the COM port.
4. `run_arduino_bridge --port COM3`.

Command-line alternative (the CLI comes bundled with the IDE):
```powershell
$cli = "C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
& $cli compile --fqbn arduino:avr:uno F:\kaggle\AI_Medical_Emergency_System\arduino\emergency_monitor
& $cli upload  --fqbn arduino:avr:uno -p COM3 F:\kaggle\AI_Medical_Emergency_System\arduino\emergency_monitor
```

Real sensors: wire one, then implement `readRealSensor()` for it. Set `SIMULATION_MODE 0`. In admin, register a separate device with mode `REAL`. A device registered as SIMULATED can never send REAL data, and the reverse is also true.
