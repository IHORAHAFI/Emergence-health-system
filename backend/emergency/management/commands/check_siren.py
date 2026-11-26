from django.core.management.base import BaseCommand
from django.test import Client
from django.test.utils import override_settings

from emergency import siren


class Command(BaseCommand):
    help = "Verify the operator siren file: exists, real MP3/WAV, duration (~6.97 s), served at its static URL."

    def handle(self, *a, **o):
        r = siren.verify()
        if not r["ok"]:
            self.stdout.write(self.style.ERROR("SIREN NOT OK: " + r["problem"]))
            return
        self.stdout.write(self.style.SUCCESS(f"Siren file OK: {r['path']} ({r['format']}, {r['bytes']} bytes, "
                                             f"{r['seconds']:.2f} s)" if r.get("seconds") else f"Siren file OK: {r}"))
        if r.get("warning"):
            self.stdout.write(self.style.WARNING(r["warning"]))
        url = siren.url()
        with override_settings(ALLOWED_HOSTS=["*"]):
            resp = Client().get(url)
        ok = resp.status_code == 200
        body = b"".join(resp.streaming_content) if getattr(resp, "streaming", False) else resp.content
        self.stdout.write((self.style.SUCCESS if ok else self.style.ERROR)(
            f"Static URL {url}: HTTP {resp.status_code}, {len(body)} bytes, {resp.get('Content-Type')}"))
