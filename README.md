# AI-Powered Smart Medical Emergency System

An academic prototype that connects patients in an emergency with suitable nearby hospitals and available
ambulances, alerts the medical team, and lets patients send IoT vital signs before they arrive.

> ⚠️ **Academic prototype.** Ambulance positions, hospital capacity and SMS are **simulated** unless configured.
> Everything simulated is labelled as simulated in the UI and the database. The AI provides **decision support
> only**: it never diagnoses and staff can always override it. An emergency request never waits for the AI.

---

## 1. What is in the project

| Part | Who uses it | Where |
|---|---|---|
| Patient web portal | Patients | `/` → register → `/patient/` |
| Ambulance crew page | Paramedics | `/crew/` |
| Control dashboard | Operators, administrators | `/dashboard/` |
| Healthcare professional dashboard | Approved hospital staff | `/dashboard/`, `/monitoring/` |
| Vital-signs monitor (live) | Staff | `/monitoring/` |
| Health information assistant (RAG) | Everyone | `/assistant/` |
| Django admin | Superuser | `/admin/` |
| REST API (+ Flutter app in `mobile_app/`) | Mobile app, devices | `/api/` |

### Folder structure
```
backend/
  config/              settings, urls, asgi (WebSockets)
  accounts/            User (roles, approval), PatientProfile, registration/profile web views, forms
  hospitals/           Hospital (services, emergency unit, status, capacity_verified), Specialty
  ambulances/          Ambulance (7 statuses), AmbulanceAssignment (dispatch record, DB-level double-booking guard)
  emergency/           EmergencyRequest, workflow services, patient portal, staff dashboard, forms, tests
  notifications/       Notification (channel + honest delivery status), SMS providers, push
  ai_recommendation/   priority model (scikit-learn), hospital recommender, ambulance ranking, geo
  medical_rag/         health information assistant: knowledge base + TF-IDF retriever
  patient_monitoring/  IoT vital signs: models, NEWS2 decision support, API, WebSocket consumer, views
  templates/           base.html, portal/, dashboard/, monitoring/, assistant/
  static/              css/app.css, js/app.js, js/ble-health.js (Web Bluetooth readers)
mobile_app/            Flutter app (patients + paramedics) using /api/
database/              PostgreSQL setup script
documentation/         system design
```

---

## 2. Install and run (Windows PowerShell)

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # then edit if needed (SQLite works with no changes)
python manage.py migrate
python manage.py seed_data      # demo hospitals, ambulances and accounts
python manage.py runserver      # http + WebSockets (Daphne)
```
Open **http://127.0.0.1:8000/**.

Web Bluetooth and GPS need **Chrome or Edge** on `http://localhost`/`127.0.0.1` or on **HTTPS**.

### Demo accounts (change before any real use)
| Role | Username | Password |
|---|---|---|
| Administrator (superuser) | `admin` | `admin12345` |
| Emergency operator | `operator` | `operator123` |
| Healthcare professional (CHUK, approved) | `chuk_staff` | `hospital123` |
| Healthcare professional (awaiting approval) | `nurse_pending` | `hospital123` |
| Patient | `patient1` | `patient123` |
| Ambulance crew | `paramedic1` … `paramedic6` | `paramedic123` |

### PostgreSQL
Run `database/setup_postgres.sql`, set `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` in `.env`, then
`python manage.py migrate`. With `DB_NAME` empty the project uses SQLite. Use PostgreSQL with encrypted
disks/backups and HTTPS for any real patient data.

---

## 3. Modules and how they work

**Authentication & roles.** Patients self-register. Healthcare professionals register with a hospital and a
licence number and stay **locked until an administrator approves them** (`/dashboard/approvals/`). Passwords are
hashed by Django with its password validators. Every page and API checks the role and checks that the
user is linked to that emergency.

**Emergency request.** `/patient/emergency/new/`. The patient shares GPS (the browser asks permission), or
picks the location on a Leaflet/OpenStreetMap map if GPS is denied or unavailable. They can also type a
landmark. Each request gets a reference such as `EMG-20261122-7F3A2C`. A second request within
`DUPLICATE_WINDOW_MINUTES` while one is active is blocked: the web page redirects to the existing request and
the API returns HTTP 409. The submit button is also disabled after one click.

**Hospital recommendation.** Hospitals are scored on distance (40%), required specialty (25%), beds/ICU
(20%) and hospital level against priority (15%), and the reasons are shown with each result. Bed numbers
are labelled *simulated/unverified* until hospital staff confirm them on the Capacity page.

**AI triage.** A RandomForest model trained on **synthetic** data suggests the priority. If the model fails, a
rule-based fallback is used (`triage_method = rule_fallback`). If dispatch itself crashes, the request is
still saved and operators get a *MANUAL DISPATCH NEEDED* alert.

**Ambulance dispatch.** The best available ambulance is chosen: the nearest, with advanced (ALS) units
preferred for high or critical cases. It is reserved with an atomic `UPDATE … WHERE status='available'`, and
a partial unique constraint on `AmbulanceAssignment` stops one ambulance from holding two open jobs, even
under concurrent requests. Statuses: Available → Assigned → Dispatched → On the way → Arrived →
(Transporting) → Available (completed), plus Out of service. Positions are **simulated** unless a crew presses
*Send my live GPS position*. Operators can step the simulated movement from the emergency page.

**Notifications.** Every alert is stored (in-app history, `/notifications/`). The patient, crew, operators,
the hospital and each of its approved staff are notified with the reference, patient details, location,
hospital, ambulance, status and latest vitals. The SMS delivery status is honest:
`sent` / `simulated` / `not_configured` / `failed`.

**Health information assistant (`medical_rag`).** It retrieves from a curated first-aid knowledge base
(summaries of WHO, NHS, IFRC and AHA guidance, with links) using TF-IDF. It answers **extractively**, so it
cannot invent facts. Red-flag phrases (for example "not breathing") show an emergency banner. If
`ANTHROPIC_API_KEY` is set, an LLM may rephrase **only** the retrieved text, and it falls back to the
extractive answer on any error.

**IoT vital signs (`patient_monitoring`).**
- After the patient gives **explicit consent** for that emergency, they open *Send my vital signs* from the
  tracking page.
- **Bluetooth:** standard Bluetooth SIG profiles via Web Bluetooth: Health Thermometer, Blood Pressure,
  Heart Rate and Pulse Oximeter (PLX). Readings are sent automatically when the device measures, with the
  device id, name, time and units (°C, bpm, %, mmHg). Device disconnection is detected and shown.
- **Manual entry** works for any device, including the rhythm shown on a portable ECG. Raw ECG traces
  (`ecg_samples`) can be posted through the API by vendor apps, because most ECGs use proprietary protocols.
- **Simulator** creates readings for normal, fever, low-oxygen or shock scenarios. These are stored as
  `source = simulated` and badged SIMULATED everywhere.
- **Validation** rejects implausible values (for example 47 °C, SpO₂ 30%), systolic ≤ diastolic, empty
  readings and future timestamps.
- **Decision support:** **NEWS2** (Royal College of Physicians), with explained alerts, a *partial* flag
  when parameters are missing, and a diastolic ≥ 120 flag. Clinicians (hospital staff and crew) can
  **confirm or override** each result. Operators can view but not review.
- **Live dashboard:** `/monitoring/` and `/monitoring/<ref>/` update over WebSockets (`/ws/vitals/…`).
  They fall back to polling if the WebSocket connection drops.
- **Alerts:** abnormal readings alert the hospital (in-app and SMS), operators and the crew. Routine readings
  go to the hospital in-app only. A failed notification never loses the measurement.
- **Audit trail:** every view, submission, consent change and review is logged in `AccessAuditLog`
  (read-only in the admin site, shown to operators and admins).

---

## 4. Tests

```powershell
cd backend
python manage.py check
python manage.py makemigrations --check
python manage.py test
```
The last run gave **83 tests, all passing**:
- `emergency/tests.py`: geo, AI priority and the API workflow.
- `emergency/test_system.py`: registration, login and approval; GPS, manual and missing location;
  duplicate prevention; hospital distance and recommendation; double-assignment prevention (including a
  simulated race); status flow; re-dispatch; simulation; notifications and SMS states; permissions for
  every role; AI, recommender and dispatch failure.
- `medical_rag/tests.py`: retrieval, sources, red flags, unknown topics and failure handling.
- `patient_monitoring/tests.py`: NEWS2 bands; consent; Bluetooth/manual/simulated readings; implausible
  and missing data; alerts; notification failure; advance hospital notice with vitals; access control;
  reviews; audit; WebSocket live updates and rejection of unauthorized users.

---

## 5. Demonstration scenario

1. **Patient:** log in as `patient1` → **Request emergency help** → *Share my GPS location* (allow it, or
   tap the map) → choose *Breathing problem*, tick *Difficulty breathing* → **Send**. You are taken to the
   tracking page with the reference number, the assigned ambulance, the ETA (an estimate), the destination
   hospital and the AI-recommended hospitals with their reasons.
2. **Vital signs:** on the tracking page → *Send my vital signs* → **I agree** → connect a Bluetooth
   oximeter, or choose the simulator → *Low oxygen* → **Send simulated reading**.
3. **Healthcare professional:** in another browser, log in as `chuk_staff` (or as `operator` if the AI chose
   another hospital) → **Vital signs**. The reading appears live with NEWS2, the risk and *why*. Open it →
   **Confirm** or **Override**. Alerts are listed under **Notifications**.
4. **Operator/admin:** log in as `operator` → the dashboard shows the counts, the map and the emergency →
   open it → *Simulate ambulance movement* a few times → change status to **en route → on scene →
   transporting**. Change it to **arrived** as `chuk_staff`, then **completed**. The ambulance returns to
   *Available* on the **Ambulances** page, and the patient's tracking page and notifications update.
5. **Admin:** `/dashboard/approvals/` → approve `nurse_pending`. Use `/admin/` to manage hospitals, ambulances,
   users and the audit log.

---

## 6. Feature status

**Works now, without any external service:** everything above, including GPS/manual location, maps,
recommendations, dispatch, statuses, in-app notifications, simulated SMS/push, the RAG assistant, Web
Bluetooth for standard-profile devices, the vital-signs simulator, NEWS2, live WebSockets, audit and
dashboards.

**Needs real services or credentials:**
| Feature | Needs | Configure |
|---|---|---|
| Real SMS | Africa's Talking or Twilio account | `SMS_PROVIDER` + keys in `.env` |
| Push to the Flutter app | Firebase project | `FCM_SERVER_KEY`, `NOTIFICATIONS_SIMULATE=False` |
| LLM-rephrased assistant answers | Anthropic API key | `ANTHROPIC_API_KEY` |
| Multi-process WebSockets | Redis | `pip install channels-redis`, `REDIS_URL` |
| Real ambulance tracking | GPS device or app per ambulance | crew *Send my live GPS*, or `POST /api/ambulances/mine/location/` |
| Real hospital capacity | Hospital staff updates or an HMIS integration | Capacity page |
| Proprietary BLE devices / ECG traces | Vendor SDK or app | `POST /api/monitoring/<ref>/readings/` |
| Road-based ETA | Routing service (OSRM or Google) | replace `ai_recommendation/geo.eta_minutes` |

Clinical use would also need validated triage data, ethics approval, a data-protection review and HTTPS.

---

## 7. Main API endpoints
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register/`, `/api/auth/login/` | Register / get a JWT |
| POST | `/api/emergencies/` | Create a request (409 if a duplicate) |
| GET | `/api/emergencies/{id}/` | Track a request |
| POST | `/api/emergencies/{id}/status/` | Change status |
| GET | `/api/hospitals/nearby/?lat=&lng=` | Nearby hospitals |
| POST/DELETE | `/api/monitoring/{ref}/consent/` | Give / revoke vitals consent |
| GET/POST | `/api/monitoring/{ref}/readings/` | Vitals history / send a reading |
| POST | `/api/monitoring/{ref}/readings/simulate/` | Simulated reading |
| POST | `/api/monitoring/readings/{id}/review/` | Confirm or override (clinicians) |
| WS | `/ws/vitals/` and `/ws/vitals/{ref}/` | Live vitals |

Example reading:
```json
{"source": "bluetooth", "measured_at": "2026-11-22T10:00:00Z", "spo2_pct": 95, "heart_rate_bpm": 88,
 "device": {"device_identifier": "abc123", "name": "Oximeter X", "kind": "pulse_oximeter"}}
```

---

## 8. Web frontend

Bootstrap 5, Bootstrap Icons, Chart.js 4 and Leaflet (CARTO tiles with OpenStreetMap data). One
`templates/base.html` gives logged-in users a role-aware sidebar and visitors a public top bar.

| Page | URL | Who |
|---|---|---|
| Landing | `/` | everyone |
| Patient dashboard | `/patient/` | patients |
| Emergency request | `/patient/emergency/new/` | patients |
| Monitoring dashboard (charts, map, attention list) | `/dashboard/` | operators, admins, hospital staff |
| Ambulances (register / edit) | `/dashboard/ambulances/` | operators, admins |
| Hospitals (manage, arrivals) | `/dashboard/hospitals/` | staff; editing: operators, admins |
| Patients | `/dashboard/patients/` | operators, admins |
| Users | `/dashboard/users/` | admins |
| Activity log | `/dashboard/activity/` | operators, admins |
| Medical information | `/medical/` | everyone (reviewed entries only) |
| AI medical assistant | `/assistant/` | everyone |

**Medical information review:** 14 starter entries summarised from WHO/NHS fact sheets are loaded as
*Pending clinical review* and are **hidden from patients and visitors** until an approved healthcare
professional (e.g. `chuk_staff`) or an administrator opens each one and clicks *Mark as clinically
reviewed*. Editing an entry sends it back to pending.

**Frontend files:** `static/css/app.css` (theme + responsive), `static/js/app.js` (maps, charts,
CSRF), `templates/_sidebar.html`, `templates/_field.html`, and the folders `templates/portal`,
`dashboard`, `hospitals`, `medical`, `admin_panel`, `assistant`, `monitoring`.

---

## 9. IoT vital signs: virtual patient simulator and real Bluetooth

- **Simulator** (`/monitoring/simulator/`, operators and admins): virtual patient, 8 scenarios + custom,
  five virtual IoT devices, start/pause/resume/stop/reset, speed control, live cards, charts and event log.
  Background runner: `python manage.py run_iot_simulator`.
- **Alerts** (`/monitoring/alerts/`): configurable demonstration thresholds (adult and child), stored
  alerts with device and time, acknowledgement, and notification through the existing workflow.
- **Real Bluetooth**: `static/js/ble-health.js` (Chrome/Edge) or `tools/ble_gateway.py` (Python, Bleak).

Full demonstration and test procedure: [docs/IOT_DEMO_AND_TEST_PLAN.md](docs/IOT_DEMO_AND_TEST_PLAN.md).
