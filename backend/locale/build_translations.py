"""Build locale/<lang>/LC_MESSAGES/django.po + .mo from translations_source.py (no GNU gettext required).

    pip install polib
    python locale/build_translations.py

It also lists every string marked with {% translate %} in templates that has no translation yet.
"""
import pathlib
import re
import sys

import polib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from translations_source import NOT_TRANSLATED_MEDICAL, PLURALS, T  # noqa: E402

LANGS = {"fr": (0, "nplurals=2; plural=(n > 1);"), "rw": (1, "nplurals=2; plural=(n != 1);")}
TEMPLATES = HERE.parent / "templates"


def template_msgids():
    ids = set()
    for f in TEMPLATES.rglob("*.html"):
        s = f.read_text(encoding="utf-8")
        ids.update(m.group(2) for m in re.finditer(r"""{%\s*translate\s+(["'])(.*?)\1""", s, re.S))
        for m in re.finditer(r"{%\s*blocktranslate[^%]*%}(.*?)(?:{%\s*plural\s*%}.*?)?{%\s*endblocktranslate\s*%}", s, re.S):
            ids.add(re.sub(r"{{\s*(\w+)\s*}}", r"%(\1)s", m.group(1)))
        ids.update(m.group(2) for m in re.finditer(r"""\|default:_\((["'])(.*?)\1\)""", s))
    return ids


def main():
    missing = sorted(template_msgids() - set(T) - NOT_TRANSLATED_MEDICAL - {k[0] for k in PLURALS})
    for code, (idx, plural_forms) in LANGS.items():
        po = polib.POFile()
        po.metadata = {"Project-Id-Version": "smart-medical-emergency", "Language": code,
                       "MIME-Version": "1.0", "Content-Type": "text/plain; charset=UTF-8",
                       "Content-Transfer-Encoding": "8bit", "Plural-Forms": plural_forms}
        po.header = ("DRAFT translation written by an AI assistant - must be reviewed by a native speaker.\n"
                     "Medical explanations are intentionally left untranslated (shown in English).")
        for msgid, tr in T.items():
            po.append(polib.POEntry(msgid=msgid, msgstr=tr[idx]))
        for (one, many), tr in PLURALS.items():
            po.append(polib.POEntry(msgid=one, msgid_plural=many, msgstr_plural={0: tr[idx][0], 1: tr[idx][1]}))
        out = HERE / code / "LC_MESSAGES"
        out.mkdir(parents=True, exist_ok=True)
        po.save(str(out / "django.po"))
        po.save_as_mofile(str(out / "django.mo"))
        print(f"{code}: {len(po)} entries -> {out / 'django.mo'}")
    print(f"left in English on purpose (medical): {len(NOT_TRANSLATED_MEDICAL)}")
    print("marked in templates but not translated yet:", missing or "none")


if __name__ == "__main__":
    main()
