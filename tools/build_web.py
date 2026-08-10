#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
מזריק את data/salary_data.json + config/tax_params.json לתוך web/template.html
ומייצר web/index.html - עמוד עצמאי לחלוטין שאפשר לפתוח בדפדפן או לשלוח כקובץ.

הרצה:  python3 tools/build_web.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web" / "template.html"
OUT = ROOT / "index.html"   # שורש המאגר - זהו גם מה ש-GitHub Pages מגיש
PLACEHOLDER = "/*__DATA__*/"


def main() -> int:
    data = json.loads((ROOT / "data" / "salary_data.json").read_text(encoding="utf-8"))
    data["tax_params"] = json.loads((ROOT / "config" / "tax_params.json").read_text(encoding="utf-8"))

    html = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"לא נמצא הסמן {PLACEHOLDER} בתבנית")

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # מניעת סגירה מוקדמת של תגית הסקריפט אם יופיע '</script>' בתוך הנתונים
    payload = payload.replace("</", "<\\/")

    OUT.write_text(html.replace(PLACEHOLDER, payload), encoding="utf-8")

    kb = OUT.stat().st_size / 1024
    print(f"נכתב: {OUT.relative_to(ROOT)}  ({kb:.0f} KB)")
    print(f"  פתח בדפדפן: file://{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
