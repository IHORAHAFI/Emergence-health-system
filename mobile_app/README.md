# Smart Emergency – mobile app (Flutter)

Patient and ambulance-crew app for the AI Medical Emergency System. It talks to the Django backend in `../backend`.

## Patient navigation

| Tab | What it does | Backend API |
|---|---|---|
| **Home** | SOS button, active emergency, latest vital signs, quick links (hospitals, history, diseases, AI assistant) | `/api/emergencies/`, `/api/vitals/latest/` |
| **Tests** | Visual test cards (temperature, heart rate, SpO₂, blood pressure, breathing, complete set) | `POST /api/tests/` |
| **Results** | Test status and results (refresh live), all measurements | `/api/tests/<ref>/`, `/api/vitals/latest/` |
| **Alerts** | Notifications, read / read all | `/api/notifications/` |
| **Profile** | Personal, medical and emergency-contact details, log out | `/api/auth/me/`, `/api/auth/profile/` |

## Real vs simulated measurements

* **Real Bluetooth devices** use the standard Bluetooth health profiles: Health Thermometer, Blood Pressure, Heart Rate and Pulse Oximeter. Readings are sent only while a test is *Testing*, or during an emergency after the patient agrees to share them.
* **Virtual (simulated) devices** are started by an operator in the web dashboard (`/dashboard/test-requests/`). The app only *displays* them, always with a violet **SIMULATED** badge. Real readings carry a green **REAL DEVICE** badge.
* The app never creates simulated values itself.

## Install & run

```bash
cd mobile_app
flutter pub get

# Android emulator + local backend (python manage.py runserver 0.0.0.0:8000)
flutter run

# Real phone on the same Wi-Fi as your PC
flutter run --dart-define=API_URL=http://192.168.1.10:8000/api

# Against the Render deployment
flutter run --dart-define=API_URL=https://<your-app>.onrender.com/api

# Release APK
flutter build apk --release --dart-define=API_URL=https://<your-app>.onrender.com/api
```

Bluetooth needs a real phone; an emulator has no Bluetooth.

## Tests

```bash
flutter test                                    # Bluetooth decoders, models, widgets
cd ../backend && python manage.py test api      # mobile API + per-patient access rules
```

## Manual end-to-end check

1. Seed demo data: `python manage.py seed_data`. Log in on the phone as `patient1` / `patient123`.
2. **Tests** → choose *Heart rate* → **Request**. The status shows *Sent*.
3. In a browser, log in as `operator` and open `/dashboard/test-requests/`. Accept the request and start it with a scenario.
4. On the phone, the status changes to *Testing*. Values appear with the **SIMULATED** badge, and an alert arrives.
5. To test a real device: while the test is *Testing*, tap **Connect device** and take a measurement. It appears as **REAL DEVICE**.
6. Operator completes the test → *Results* + notification.
7. **Home → SOS**: tap **Share** to send your location, choose the type and send. The tracking screen shows the ambulance and ETA. **Share vital signs** sends readings from a connected device after you agree.

## Licence note

Bluetooth uses `flutter_blue_plus` with `License.nonprofit`, which covers educational use. Commercial use needs its paid licence (see `lib/services/ble_service.dart`).
