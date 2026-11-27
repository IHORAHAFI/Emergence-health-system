"""Arduino IoT pipeline: serial parser, Django ingest validation, bridge token, dashboards, patient isolation,
and the bridge itself end-to-end against the virtual Arduino (no physical board involved)."""
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from .bridge import serial_bridge as sb
from .bridge.serial_parser import ParseError, parse_line
from .models import IoTDevice, SensorReading

TOKEN = "test-bridge-token"
INGEST = "/api/iot/ingest/"
VIRTUAL = Path(settings.BASE_DIR).parent / "arduino" / "tools" / "virtual_arduino.py"


def reading(seq=1, **kw):
    return {"type": "reading", "device_id": "ARDUINO_DEVICE_01", "boot_id": "ABCD1234", "seq": seq,
            "uptime_ms": 5000, "sensor_type": "temperature", "value": 36.8, "unit": "C", "source": "SIMULATED",
            "status": "VALID", "received_at": timezone.now().isoformat(), **kw}


class ParserTests(TestCase):
    def test_valid_line(self):
        m = parse_line(b'{"type":"reading","device_id":"D","boot_id":"B","seq":1,"sensor_type":"spo2",'
                       b'"value":97,"unit":"%","source":"SIMULATED","status":"VALID"}\r\n')
        self.assertEqual(m["seq"], 1)

    def test_malformed(self):
        for bad in (b"", b"garbage", b'{"type":"reading","device_id":"D"', b'{"type":"x","device_id":"D"}',
                    b"\xff\xfe{}", b'{"type":"reading","device_id":"D","seq":"1"}', b"[1,2]"):
            with self.assertRaises(ParseError, msg=bad):
                parse_line(bad)


@override_settings(ARDUINO_BRIDGE_TOKEN=TOKEN)
class IngestTests(TestCase):
    def setUp(self):
        self.c = APIClient()
        self.c.credentials(HTTP_X_BRIDGE_TOKEN=TOKEN)
        self.patient = User.objects.create_user("pat", password="x", role="patient")
        self.other = User.objects.create_user("pat2", password="x", role="patient")
        self.dev = IoTDevice.objects.create(device_id="ARDUINO_DEVICE_01", mode="SIMULATED", approved=True)

    def send(self, *msgs):
        r = self.c.post(INGEST, {"port": "COM9", "messages": list(msgs)}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return [x["result"] for x in r.json()["results"]], r.json()["results"]

    def test_token_required(self):
        bad = APIClient()
        self.assertEqual(bad.post(INGEST, {"messages": [reading()]}, format="json").status_code, 403)
        bad.credentials(HTTP_X_BRIDGE_TOKEN="wrong")
        self.assertEqual(bad.post(INGEST, {"messages": [reading()]}, format="json").status_code, 403)
        with override_settings(ARDUINO_BRIDGE_TOKEN=""):
            self.assertEqual(self.c.post(INGEST, {"messages": [reading()]}, format="json").status_code, 503)

    def test_saved_with_all_fields_and_pushed(self):
        layer = get_channel_layer()
        ch = "iot-test-channel"
        async_to_sync(layer.group_add)("operators", ch)
        res, _ = self.send(reading())
        self.assertEqual(res, ["saved"])
        r = SensorReading.objects.get()
        self.assertEqual((r.device.device_id, r.sensor_type, float(r.value), r.unit, r.source, r.status, r.method),
                         ("ARDUINO_DEVICE_01", "temperature", 36.8, "C", "SIMULATED", "VALID", "usb_serial"))
        self.assertIsNone(r.patient)  # never linked to a patient automatically
        msg = async_to_sync(layer.receive)(ch)
        self.assertEqual((msg["kind"], msg["id"]), ("iot", r.id))
        self.dev.refresh_from_db()
        self.assertTrue(self.dev.connected)
        self.assertEqual(self.dev.port, "COM9")

    def test_duplicates(self):
        self.assertEqual(self.send(reading(), reading())[0], ["saved", "duplicate"])
        self.assertEqual(SensorReading.objects.count(), 1)
        self.assertEqual(self.send(reading(boot_id="NEWBOOT1"))[0], ["saved"])  # board reset: new boot id

    def test_unknown_device_pending(self):
        res, out = self.send(reading(device_id="STRANGER"))
        self.assertEqual(res, ["rejected"])
        self.assertFalse(IoTDevice.objects.get(device_id="STRANGER").approved)
        self.assertEqual(self.send(reading(device_id="STRANGER"))[0], ["rejected"])
        self.assertFalse(SensorReading.objects.exists())

    def test_mode_mismatch_refused(self):
        res, out = self.send(reading(source="REAL"))
        self.assertEqual(res, ["rejected"])
        self.assertIn("registered as SIMULATED", out[0]["detail"])

    def test_invalid_values(self):
        cases = [reading(seq=1, value=55.0), reading(seq=2, unit="F"), reading(seq=3, sensor_type="glucose"),
                 reading(seq=4, status="INVALID", value=50), reading(seq=5, value="37"),
                 reading(seq=6, received_at=(timezone.now() + timezone.timedelta(hours=1)).isoformat()),
                 reading(seq=7, received_at="yesterday"), reading(seq=8, value=None)]
        self.assertEqual(self.send(*cases)[0], ["rejected"] * len(cases))
        self.assertFalse(SensorReading.objects.exists())

    def test_error_status_stored_without_value_and_not_connected_only_status(self):
        res, _ = self.send(reading(seq=1, status="SENSOR_ERROR", value=None),
                           reading(seq=2, sensor_type="spo2", unit="%", status="NOT_CONNECTED", value=None))
        self.assertEqual(res, ["saved", "status"])
        self.assertIsNone(SensorReading.objects.get().value)
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.sensor_state["spo2"], "NOT_CONNECTED")

    def test_heartbeat_and_connection(self):
        hb = {"type": "heartbeat", "device_id": "ARDUINO_DEVICE_01", "boot_id": "ABCD1234", "mode": "SIMULATED",
              "received_at": timezone.now().isoformat()}
        self.assertEqual(self.send(hb)[0], ["status"])
        r = self.c.post("/api/iot/connection/", {"device_id": "ARDUINO_DEVICE_01", "connected": False}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["device"]["online"])

    def test_patient_sees_only_assigned_readings(self):
        self.send(reading(seq=1))                       # before assignment: no patient
        self.dev.patient = self.patient
        self.dev.save()
        self.send(reading(seq=2))
        mine = APIClient()
        mine.force_authenticate(self.patient)
        d = mine.get("/api/iot/my-readings/").json()
        self.assertEqual([r["patient_id"] for r in d["readings"]], [self.patient.id])
        self.assertTrue(d["readings"][0]["simulated"])
        theirs = APIClient()
        theirs.force_authenticate(self.other)
        self.assertEqual(theirs.get("/api/iot/my-readings/").json()["readings"], [])

    def test_dashboard_feed_permissions(self):
        self.send(reading())
        op = User.objects.create_user("op", password="x", role="operator")
        self.client.force_login(op)
        d = self.client.get("/dashboard/iot.json").json()
        self.assertEqual(len(d["readings"]), 1)
        self.assertEqual(d["devices"][0]["mode"], "SIMULATED")
        self.client.force_login(self.patient)
        self.assertNotEqual(self.client.get("/dashboard/iot.json").status_code, 200)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeResponse:
    def __init__(self, resp):
        self.status_code, self._data, self.content, self.text = resp.status_code, resp.json(), resp.content, ""

    def json(self):
        return self._data


@override_settings(ARDUINO_BRIDGE_TOKEN=TOKEN)
class BridgeEndToEndTests(TransactionTestCase):
    """Runs the real bridge against the virtual Arduino over socket://; HTTP goes to the Django test client."""

    def setUp(self):
        IoTDevice.objects.create(device_id="ARDUINO_DEVICE_01", mode="SIMULATED", approved=True)
        self.tmp = Path(tempfile.mkdtemp())
        self.api = APIClient()
        self.api.credentials(HTTP_X_BRIDGE_TOKEN=TOKEN)
        self.down = False

    def fake_post(self, url, json=None, timeout=None):
        if self.down:
            import requests
            raise requests.ConnectionError("backend down")
        return FakeResponse(self.api.post(url.replace("http://test", ""), json, format="json"))

    def start_virtual(self, *extra):
        port = free_port()
        proc = subprocess.Popen([sys.executable, "-I", str(VIRTUAL), "--port", str(port), "--interval", "1", *extra],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        return port

    def bridge(self, port, seconds):
        b = sb.Bridge(f"socket://127.0.0.1:{port}", 115200, "http://test", TOKEN, self.tmp, once_seconds=seconds)
        b.http.post = self.fake_post
        b.conn_http.post = self.fake_post
        return b

    def test_stream_malformed_and_spool(self):
        port = self.start_virtual("--glitch", "0.2")
        b = self.bridge(port, 8)
        b.run()
        n = SensorReading.objects.count()
        self.assertGreaterEqual(n, 3, b.stats)
        self.assertTrue(all(r.source == "SIMULATED" for r in SensorReading.objects.all()))
        self.assertEqual(b.stats.get("rejected", 0), 0)

        # backend unavailable: readings go to the spool, then are delivered when it comes back
        self.down = True
        b2 = self.bridge(port, 5)
        b2.run()
        self.assertGreater(b2.spool_count(), 0)
        self.assertEqual(SensorReading.objects.count(), n)
        self.down = False
        spooled = b2.spool_count()
        b3 = self.bridge(port, 6)
        b3.run()
        self.assertEqual(b3.spool_count(), 0)
        self.assertGreaterEqual(SensorReading.objects.count(), n + 1)  # readings (heartbeats aren't rows)

    def test_reconnect_after_disconnect(self):
        port = self.start_virtual("--drop-after", "2")
        b = self.bridge(port, 7)
        b.run()
        self.assertGreaterEqual(b.stats["disconnects"], 1, b.stats)
        boots = set(SensorReading.objects.values_list("boot_id", flat=True))
        self.assertGreaterEqual(len(boots), 2, "should have reconnected and received a new boot")

    def test_missing_port_does_not_crash(self):
        b = self.bridge(free_port(), 1.5)  # nothing listening: waits and retries, then exits cleanly
        b.run()
        self.assertEqual(json.loads((self.tmp / "status.json").read_text())["state"], "stopped")

    def test_single_instance_lock(self):
        (self.tmp / "bridge.lock").write_text(str(__import__("os").getpid() + 0))
        with mock.patch.object(sb.os, "getpid", return_value=1):
            with self.assertRaises(RuntimeError):
                sb.acquire_lock(self.tmp)
