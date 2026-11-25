# System design and evaluation guide

## 1. Analysis (Objective 1): problems addressed
| Current challenge | System response |
|---|---|
| The patient cannot describe their location | GPS capture with accuracy in metres |
| The patient doesn't know which hospital can treat them | AI ranks hospitals by specialty, capacity, level and distance |
| Finding a free ambulance takes time | Automatic nearest-available assignment (simulated) |
| The hospital is unprepared when the patient arrives | Pre-arrival alert with priority and symptoms |
| No visibility of progress | Live status timeline for patient, operator and hospital |

Suggested data collection: short questionnaires or interviews with patients, ambulance staff and ER staff covering response-time pain points.

## 2. Design (Objective 2)
### Actors
Patient, Paramedic, Emergency Operator, Hospital Staff, Administrator.

### Entities (ERD)
```
User(id, username, role, phone, fcm_token, hospital_id→Hospital)
PatientProfile(user_id→User, dob, blood_group, allergies, chronic_conditions, emergency_contact)
Specialty(id, code, name)
Hospital(id, name, lat, lng, level, total_beds, available_beds, icu_available, has_emergency_unit) *—* Specialty
Ambulance(id, plate, type[BLS/ALS], status, lat, lng, base_hospital_id, driver_id→User)
EmergencyRequest(id, patient_id, type, symptoms…, lat, lng, priority, ai_confidence, recommendations[json],
                 status, assigned_hospital_id, assigned_ambulance_id, eta_minutes, timestamps)
StatusLog(id, emergency_id, status, note, by, at)
Notification(id, recipient_id|hospital_id, emergency_id, channel, title, message, delivered, is_read)
```

### Emergency status state machine
```
pending → assigned → en_route → on_scene → transporting → arrived → completed
   └──────────┴──────────┴──────────┴→ cancelled
```

### Sequence
```
Patient app ─POST /emergencies─▶ Django
   Django: predict_priority() → recommend_hospitals() → select_ambulance()
   Django ─push/SMS─▶ Patient · Paramedic · Hospital
Paramedic app ─POST location (GPS stream)─▶ Django
Paramedic app ─POST status─▶ Django ─push─▶ Patient / Hospital
Patient app ─GET /emergencies/{id} every 5 s─▶ Django (tracking)
```

## 3. AI components
- **Priority model**: RandomForest (150 trees) over 8 features (type, age, consciousness, breathing, bleeding, chest pain, pain level, pregnancy). Trained on 5,000 synthetic rule-derived samples with 5% label noise. `python manage.py train_priority_model` prints precision/recall/F1.
- **Hospital recommender**: a transparent weighted score that returns human-readable reasons.
- **Ambulance selection**: minimum haversine distance, with a +3 km penalty on BLS units for high and critical cases.

## 4. Testing and evaluation (Objective 4)
| Criterion | How to measure | Where |
|---|---|---|
| Location accuracy | Compare app GPS with known reference points; record `location_accuracy_m` | Mobile + DB |
| Distance calc | Haversine against known distances (unit test) | `emergency/tests.py` |
| Hospital identification | Nearby list sorted; cardiac → cardiology hospital | tests |
| Ambulance assignment | Assigned and dispatched; pending when none free; freed after completion | tests |
| Notification | Patient and hospital notifications created | tests |
| AI performance | Classification report (accuracy, F1) | `train_priority_model` |
| Response time | Avg request→dispatch time, avg ETA, assignment rate | `/dashboard/reports/` |

Simulation procedure: log in as operator, then press **Simulate emergency** N times (e.g. 50) and record the values on the Reports page.
Also run usability tests with 5–10 users on the mobile app (SUS questionnaire).

## 5. Limitations and future work
- Straight-line distance × 1.3 road factor; replace it with OSRM or Google Directions for real routes.
- Synthetic training data; use real, de-identified, ethically approved data.
- Polling for live tracking; WebSockets (Django Channels) can replace it.
- Integration with Rwanda's 912 / SAMU would require an official partnership.
