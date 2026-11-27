"""Turns one raw serial line into a dict, or raises ParseError. Structural checks only: the backend
(patient_monitoring/iot_ingest.py) is the authority on whether a reading is medically plausible."""
import json

MAX_LINE = 512
TYPES = {"hello", "reading", "heartbeat", "ack", "error"}
FORWARDED = {"hello", "reading", "heartbeat"}  # ack/error are replies to commands: logged, not stored


class ParseError(ValueError):
    pass


def parse_line(raw):
    if isinstance(raw, bytes):
        if len(raw) > MAX_LINE:
            raise ParseError(f"line longer than {MAX_LINE} bytes")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ParseError("not UTF-8 (wrong baud rate?)")
    line = raw.strip()
    if not line:
        raise ParseError("empty line")
    if not line.startswith("{") or not line.endswith("}"):
        raise ParseError("not a JSON object (partial line or boot noise)")
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid JSON: {e.msg}")
    if not isinstance(msg, dict):
        raise ParseError("JSON is not an object")
    if msg.get("type") not in TYPES:
        raise ParseError(f"unknown message type {msg.get('type')!r}")
    if not isinstance(msg.get("device_id"), str) or not msg["device_id"]:
        raise ParseError("device_id missing")
    if msg["type"] == "reading":
        for k in ("sensor_type", "unit", "source", "status", "boot_id"):
            if not isinstance(msg.get(k), str):
                raise ParseError(f"reading without {k}")
        if not isinstance(msg.get("seq"), int):
            raise ParseError("reading without integer seq")
    return msg


def reading_key(msg):
    """Identity of a reading for duplicate detection, or None for non-readings."""
    if msg.get("type") != "reading":
        return None
    return (msg["device_id"], msg["boot_id"], msg["seq"])
