"""The operator siren file (ALERT_SIREN_FILE, default static/audio/police_wail_emergency_siren.mp3).

verify() inspects the REAL file - it never assumes a file exists because a path appears in the code:
  exists? not empty? really MP3 / WAV (magic bytes)? approximate duration (expected ~6.97 s)?
The dashboards only get a siren URL when verify() passes; otherwise they show a visible "siren file" error.
"""
from pathlib import Path

from django.conf import settings

EXPECTED_SECONDS = 6.97
_MP3_BITRATES = {  # MPEG-1 Layer III, kbit/s by index
    1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96, 8: 112, 9: 128, 10: 160, 11: 192, 12: 224, 13: 256, 14: 320}
_MP3_RATES = {0: 44100, 1: 48000, 2: 32000}


def name():
    return getattr(settings, "ALERT_SIREN_FILE", "audio/police_wail_emergency_siren.mp3")


def path():
    from django.contrib.staticfiles import finders
    found = finders.find(name())
    return Path(found) if found else None


def _skip_id3(b):
    if b[:3] == b"ID3" and len(b) > 10:
        size = (b[6] & 0x7F) << 21 | (b[7] & 0x7F) << 14 | (b[8] & 0x7F) << 7 | (b[9] & 0x7F)
        return 10 + size
    return 0


def _mp3_duration(b):
    """Duration in seconds: Xing/Info frame count if present (VBR), else size / bitrate (CBR)."""
    i = _skip_id3(b)
    while i + 4 <= len(b) and not (b[i] == 0xFF and (b[i + 1] & 0xE0) == 0xE0):
        i += 1
    if i + 4 > len(b):
        return None
    h = b[i:i + 4]
    version, layer = (h[1] >> 3) & 3, (h[1] >> 1) & 3
    br_idx, sr_idx = h[2] >> 4, (h[2] >> 2) & 3
    if layer != 1 or br_idx not in _MP3_BITRATES or sr_idx not in _MP3_RATES:
        return None  # not Layer III (or unusual) - cannot estimate, still playable
    rate = _MP3_RATES[sr_idx] // (1 if version == 3 else 2 if version == 2 else 4)
    samples_per_frame = 1152 if version == 3 else 576
    for tag in (b"Xing", b"Info"):
        x = b.find(tag, i, i + 200)
        if x != -1 and b[x + 7] & 1:
            frames = int.from_bytes(b[x + 8:x + 12], "big")
            return frames * samples_per_frame / rate
    return (len(b) - i) * 8 / (_MP3_BITRATES[br_idx] * 1000)


def _wav_duration(b):
    if len(b) < 44:
        return None
    byte_rate = int.from_bytes(b[28:32], "little")
    data = b.find(b"data")
    if data == -1 or not byte_rate:
        return None
    return int.from_bytes(b[data + 4:data + 8], "little") / byte_rate


_cache = {}


def verify():
    """{'ok', 'problem', 'path', 'format', 'seconds', 'bytes'} - cached until the file changes."""
    p = path()
    key = (str(p), p.stat().st_mtime if p is not None and p.exists() else None)
    if key in _cache:
        return _cache[key]
    _cache.clear()
    _cache[key] = r = _verify(p)
    return r


def _verify(p):
    if p is None or not p.exists():
        return {"ok": False, "problem": f"Siren file not found: static/{name()} - add the Police Wail MP3 there."}
    b = p.read_bytes()
    out = {"path": str(p), "bytes": len(b)}
    if len(b) < 1000:
        return {**out, "ok": False, "problem": "Siren file is empty or too small to be audio."}
    if b[:4] == b"RIFF" and b[8:12] == b"WAVE":
        out.update(format="wav", seconds=_wav_duration(b))
    elif b[:3] == b"ID3" or (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0):
        out.update(format="mp3", seconds=_mp3_duration(b))
    else:
        return {**out, "ok": False, "problem": "Siren file is not an MP3 or WAV file (wrong format)."}
    secs = out.get("seconds")
    out["ok"] = True
    out["problem"] = ""
    if secs is not None and abs(secs - EXPECTED_SECONDS) > 1.0:
        out["warning"] = f"Duration is {secs:.2f} s, expected about {EXPECTED_SECONDS} s - is this the right file?"
    return out


def url():
    """Static URL of a verified siren file, or '' (the dashboards then show a visible error)."""
    from django.templatetags.static import static
    try:
        return static(name()) if verify()["ok"] else ""
    except Exception:  # manifest storage without collectstatic
        return ""
