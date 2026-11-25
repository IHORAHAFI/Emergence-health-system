# IoT vital signs: demonstration and test plan

AI-Powered Smart Medical Emergency System - virtual patient, virtual IoT devices, real Bluetooth.

All simulated data is stored and displayed as **SIMULATED**. Scenario values are illustrative
trajectories for demonstration, not predictions of real physiology. Alert thresholds are
demonstration limits, configurable by an administrator, not universal clinical rules.

---

## 1. How a measurement travels

**Real device**

```
Bluetooth medical sensor (standard GATT profile)
        |  BLE notifications / indications (IEEE-11073 bytes)
        v
Chrome / Edge (static/js/ble-health.js)   or   Python gateway (tools/ble_gateway.py, Bleak)
        |  decode bytes -> JSON, HTTPS/localhost POST
        v
Django API  POST /api/monitoring/<emergency>/readings/   (permission + consent checked)
        |  validate -> NEWS2 score -> threshold check
        v
SQLite (VitalSignReading, EmergencyAlert, AccessAuditLog)
        |  WebSocket broadcast (Django Channels) + notifications
        v
Dashboards (vital signs, alerts, emergency dashboard) -> hospital, operators, ambulance crew
```

**Virtual device**

```
Virtual patient (scenario trajectory + noise)
        |
Five virtual IoT devices (each with ID, battery, connection, own transmit interval)
        |
Simulation engine (patient_monitoring/simulation.py), driven by the control panel
or by:  python manage.py run_iot_simulator
        |  same function real devices use: services.record_reading(source=SIMULATED)
        v
Django backend -> SQLite -> live control panel, alerts, emergency dashboard
```

MQTT/Mosquitto is not used: the Django API plus Channels already provide transmission and live
updates without running an extra broker. It can be added later as another input to
`services.record_reading` if required.

---

## 2. Start everything

```powershell
cd F:\kaggle\AI_Medical_Emergency_System\backend
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Optional second terminal (keeps simulations running even with the browser closed):

```powershell
cd F:\kaggle\AI_Medical_Emergency_System\backend
python manage.py run_iot_simulator
```

Logins: `operator` / `operator123` (simulator, dashboard), `admin` / `admin12345` (thresholds),
`chuk_staff` / `hospital123` (hospital view). Always open **http://127.0.0.1:8000** - Web Bluetooth
is blocked on `http://192.168.x.x` addresses.

## 3. Pages

| Page | URL | Purpose |
|---|---|---|
| IoT simulator | `/monitoring/simulator/` | create virtual patient, choose scenario |
| Simulation control panel | `/monitoring/simulator/<id>/` | start/pause/resume/stop/reset, speed, devices, live cards, charts, readings, event log |
| Vital-sign alerts | `/monitoring/alerts/` | open alerts, acknowledge, edit thresholds (admin) |
| Send vital signs | `/monitoring/<ref>/measure/` | real Bluetooth mode, manual entry, quick simulation, live cards and chart |
| Clinical vital-signs view | `/monitoring/<ref>/` | NEWS2, clinician confirm/override, audit |
| Emergency dashboard | `/dashboard/` | open vital-sign alerts appear at the top |

---

## 4. Test procedure

"Automated" results come from `python manage.py test` (131 tests, all passing on 2026-11-23).
Fill in the screenshot column during the live demonstration.

| # | Test | Steps | Expected result | Actual result | Pass/fail | Screenshot |
|---|---|---|---|---|---|---|
| 1 | Healthy virtual patient | Simulator -> new patient -> **Healthy person** -> Create -> Start | Values vary slightly around 36.7 °C, 76 bpm, 98 %, 120/80, 16/min; all cards "Normal range"; no alerts | Automated: values vary, stay plausible, diastolic < systolic, nothing flagged | Pass | |
| 2 | Fever | Scenario **Fever**, speed 5x, Start | Temperature climbs gradually (no jump) towards ~39.3 °C; card turns "Abnormal (> 38)"; alert raised | Automated: temperature never decreases, half-way value is in between, alert created above 38 °C | Pass | |
| 3 | Elevated heart rate | Scenario **Elevated heart rate** | HR rises to ~140 bpm in ~10 simulated minutes; "Abnormal (> 120)" | Covered by engine tests; verify live | | |
| 4 | Low SpO2 | Scenario **Low oxygen saturation** | SpO2 falls to ~85 %; "Abnormal (< 92)"; alert to medical team | Automated page flow test with this scenario passes | Pass | |
| 5 | Abnormal blood pressure | Scenario **Abnormal blood pressure** | BP rises to ~190/122; systolic "> 180" and diastolic "> 120" alerts | Verify live | | |
| 6 | Accident victim | Scenario **Accident victim** | Sudden change at minute 3 (HR 76 -> 118), then gradual deterioration | Automated: HR exactly 76 at minute 2 and 118 at minute 3 | Pass | |
| 7 | Several devices at once | Any scenario, Start, watch the device table | Oximeter and HR sensor transmit every minute, respiratory every 2, thermometer every 3, BP every 5; each stored reading names its own device ID | Automated: after 6 minutes 6 / 3 / 2 / 1 transmissions as expected; each record identifies its device; none stored as real | Pass | |
| 7b | Virtual disconnection and battery | Press **Disconnect** on a device; set a battery low (admin) | No data while disconnected; transmits again after Connect; empty battery disconnects the device and logs an event | Automated: 0 transmissions while disconnected, 1 after reconnect; "battery empty" event logged | Pass | |
| 8 | Real Bluetooth discovery and connection | Phone with **nRF Connect** -> Configure GATT server -> add Heart Rate service -> advertise. PC: Send vital signs page -> Heart-rate sensor -> Connect -> choose the phone | Status "Connected"; each value sent from the phone appears as a **BLUETOOTH** reading in the cards, chart and database | Not run yet (no compatible device). Decoders verified with 11 byte samples in Chrome and 10 in Python. Python gateway scan found a nearby BLE device on this PC | Pending | |
| 9 | Bluetooth disconnection and recovery | During test 8, switch off advertising on the phone, then switch it on again | "Reconnecting (1/3)...", then "Connected" again; after 3 failures "Disconnected" with instructions | Not run yet (needs the device from test 8) | Pending | |
| 10 | Alert generation and dashboard | Run scenario **Critical emergency**; open Dashboard and Vital-sign alerts; acknowledge one | Red banner on the panel; "open vital-sign alert" card on the emergency dashboard; alert lists value, limit, device, time and patient; notification sent once per 5 minutes, not per reading; acknowledge records who and when | Automated: alert has value, limit, device and time; workflow notified; repeated readings do not create duplicates; acknowledge records the user; dashboard shows the alert; patients get 403 | Pass | |

Additional automated safety checks (all passing): simulator refuses to write to a real patient's
emergency; patients and hospital staff cannot open the simulator; a step is never processed twice
when the page and the background command run together; only administrators can change thresholds;
children (< 16) are checked against child limits.

## 5. Real Bluetooth: compatibility

Works with devices implementing the standard Bluetooth SIG profiles:

| Device | Service | Measurement characteristic |
|---|---|---|
| Thermometer | Health Thermometer 0x1809 | Temperature Measurement 0x2A1C (indicate) |
| Blood pressure | Blood Pressure 0x1810 | Blood Pressure Measurement 0x2A35 (indicate) |
| Heart rate | Heart Rate 0x180D | Heart Rate Measurement 0x2A37 (notify) |
| Pulse oximeter | Pulse Oximeter 0x1822 | PLX Spot-check 0x2A5E / Continuous 0x2A5F |

Requirements: Chrome or Edge (not Firefox, Safari or any iPhone browser); page opened via
`http://127.0.0.1`, `http://localhost` or HTTPS; Bluetooth on.

If a device does not appear, tick **Show all nearby Bluetooth devices**. If it then says the device
"does not offer the standard ... service" and lists other services, it uses a **proprietary
protocol** (common for cheap oximeters and most ECGs). Reading it requires the manufacturer's service
UUID, characteristic UUID and byte format from its documentation or SDK; a parser for that format
must then be added to `PROFILES` in `ble-health.js` and `tools/ble_gateway.py`. Byte formats for
medical data must never be guessed.

Python gateway (alternative to the browser):

```powershell
pip install bleak requests
python tools\ble_gateway.py scan --services
python tools\ble_gateway.py read --address AA:BB:CC:DD:EE:FF --kind heart_rate --emergency EMG-... --username paramedic1 --password paramedic123
```

## 6. Files

```
backend/patient_monitoring/
    models.py            VitalSignReading, MedicalDevice, VirtualPatient, SimulationSession,
                         VirtualDevice, SimulationEvent, AlertThreshold, EmergencyAlert
    simulation.py        simulation engine: scenarios, trajectories, devices, controls, tick
    services.py          record_reading (shared by real + virtual), thresholds, alerts, notifications
    sim_views.py         simulator pages and JSON endpoints
    views.py             send vital signs page, clinical view, alerts page
    api.py               REST API used by browsers, the Flutter app and the BLE gateway
    management/commands/run_iot_simulator.py
    test_simulation.py   simulator + alert tests
backend/static/js/ble-health.js            Web Bluetooth (standard GATT profiles)
backend/tools/ble_gateway.py               Python BLE gateway (Bleak)
backend/templates/monitoring/              measure, simulator_home, simulator_panel, alerts, board, detail
```

---

## 7. Patient-initiated medical tests (patient -> operator -> IoT -> patient)

```
Patient: Medical tests page -> Request Test -> MedicalTestRequest (PENDING, ID TST-YYYYMMDD-XXXX)
   -> in-app notification to every operator + WebSocket "operators" (+ polling every 5 s)
Operator (any page): red badge + popup + chime every 20 s (max 10x) until accepted
   -> Patient test requests -> View details (patient profile, history) -> Accept -> Start IoT test
   -> IoT session for THIS request's patient; only the requested devices; readings stored as SIMULATED
   -> WebSocket "patient_<id>" (+ polling every 3 s) -> patient's results page updates by itself
   -> Complete test: simulation stops, patient notified "Your test results are ready"
```

Accounts: `patient1` / `patient123` (Patient A), `patient2` / `patient123` (Patient B), `operator` / `operator123`.
Pages: patient **Medical tests & results** (`/patient/tests/`); operator **Patient test requests**
(`/dashboard/test-requests/`). Operator: press **Enable sound** in the top bar once per browser.

| # | Scenario | Expected | Actual (2026-11-23) | Result |
|---|---|---|---|---|
| 1 | Patient A requests temperature; operator hears alarm, runs thermometer | Request PENDING with TST ID; operator badge + popup + chime; only the thermometer runs; temperatures appear on Patient A's page | Automated test passes. Real Chrome: badge 1, popup, chime 880/660 Hz played on the operator page without reload; Patient A's page showed "Temperature 36.7 °C · Digital thermometer (virtual) · SIMULATED" without reload | Pass |
| 2 | Patient B requests heart rate | Only the heart-rate sensor runs; readings only on Patient B | Automated: device list = [heart_rate]; all readings belong to Patient B; none to Patient A | Pass |
| 3 | Patient A requests all vital signs | Five devices; five result cards | Automated: 5 devices; results contain temperature, heart rate, SpO2, blood pressure, respiratory rate; page shows SIMULATED | Pass |
| 4 | Two patients at the same time | Both appear; managed independently | Automated: pending count 2; completing A leaves B testing; each request's readings belong only to its patient | Pass |
| 5 | Pause and resume | No readings while paused; continue after resume | Automated: reading count unchanged while paused, increases after resume | Pass |
| 6 | Operator completes test | Simulation stops; no new readings; patient notified with results | Automated + live server: status completed, simulation stopped, readings 10 before / 10 after extra ticks, notification "Your test results are ready" with values and SIMULATED note; Chrome page switched to Completed by itself | Pass |
| 7 | Patient A opens Patient B's results | Denied | Live server: 404 for results page and data endpoint; automated: also cannot cancel B's request | Pass |

Also verified: duplicate and overlapping requests are refused; no measurement can be stored unless the request is
TESTING; cancelling a running test stops it; patients, hospital staff and ambulance crews get 403 on operator pages;
accepting stops the alarm (Chrome: no tone in the 25 s after Accept, ring counter cleared); a critical result plays the
different alarm (Chrome: 1050/700 Hz x6 with a CRITICAL popup); Patient B receives no WebSocket events about Patient A.
Not observed in a browser: the repeat ring every 20 s for a request left unaccepted (the logic is in
`static/js/operator-alerts.js`).
