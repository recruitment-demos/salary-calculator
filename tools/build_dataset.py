#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETL: קורא את קבצי המקור מתוך 'נתונים למערכת' ובונה את data/salary_data.json.

הרעיון: אף מספר לא מוקלד ידנית. כל ערך נשאב ישירות מטבלאות ה-PPTX/DOCX,
כך שאם קובץ מקור מתעדכן - מריצים מחדש ומקבלים מערכת מעודכנת.

הרצה:  python3 tools/build_dataset.py
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "נתונים למערכת"
OUT = ROOT / "data" / "salary_data.json"

NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

GEMUL_NONE = "ללא גמול השתלמות"
GEMUL_A = "כולל גמול השתלמות א'"


# --------------------------------------------------------------------------
# עזרי טקסט
# --------------------------------------------------------------------------

# תווי כיווניות/רווחים בלתי-שבירים שמופיעים בקבצי אופיס ומשבשים השוואות
_BIDI = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮⁦⁧⁨⁩"), None)


def clean(text: str) -> str:
    """נרמול טקסט: הסרת תווי כיווניות, איחוד גרשיים ורווחים."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).translate(_BIDI)
    text = text.replace(" ", " ")
    # איחוד כל צורות הגרש/גרשיים לצורה אחת, כולל '' כתחליף ל-"
    text = text.replace("״", '"').replace("”", '"').replace("“", '"')
    text = text.replace("׳", "'").replace("’", "'").replace("‘", "'")
    text = text.replace("''", '"')
    return re.sub(r"\s+", " ", text).strip()


def parse_money(text: str) -> int | None:
    """'11,207₪' / '14,546  ₪' -> 11207 / 14546"""
    digits = re.sub(r"[^\d]", "", clean(text))
    return int(digits) if digits else None


def gemul_key(label: str) -> str | None:
    label = clean(label)
    if label.startswith("ללא גמול"):
        return "no_gemul"
    if label.startswith("כולל גמול"):
        return "gemul_a"
    return None


# --------------------------------------------------------------------------
# קריאת טבלאות מקבצי אופיס
# --------------------------------------------------------------------------


def pptx_slides(path: Path) -> list[dict]:
    """מחזיר לכל שקופית: כותרת (כל הטקסט שאינו בטבלה) ורשימת טבלאות."""
    slides = []
    with zipfile.ZipFile(path) as z:
        names = sorted(
            (n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)", n).group(1)),
        )
        for name in names:
            root = ET.fromstring(z.read(name))
            tables, in_table = [], set()
            for tbl in root.iter(NS_A + "tbl"):
                rows = []
                for tr in tbl.iter(NS_A + "tr"):
                    cells = []
                    for tc in tr.iter(NS_A + "tc"):
                        parts = [t.text or "" for t in tc.iter(NS_A + "t")]
                        in_table.update(id(t) for t in tc.iter(NS_A + "t"))
                        cells.append(clean("".join(parts)))
                    rows.append(cells)
                tables.append(rows)
            free_text = [
                clean(t.text or "") for t in root.iter(NS_A + "t") if id(t) not in in_table
            ]
            slides.append(
                {
                    "file": path.name,
                    "slide": name,
                    "text": [t for t in free_text if t],
                    "tables": tables,
                }
            )
    return slides


def docx_blocks(path: Path) -> list[tuple[str, object]]:
    """מחזיר רצף מסודר של ('p', טקסט) ו-('tbl', שורות) כפי שהם בקובץ."""
    blocks = []
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(NS_W + "body")
    for el in body:
        if el.tag == NS_W + "p":
            txt = clean("".join(t.text or "" for t in el.iter(NS_W + "t")))
            if txt:
                blocks.append(("p", txt))
        elif el.tag == NS_W + "tbl":
            rows = []
            for tr in el.iter(NS_W + "tr"):
                cells = [
                    clean("".join(t.text or "" for t in tc.iter(NS_W + "t")))
                    for tc in tr.iter(NS_W + "tc")
                ]
                rows.append(cells)
            blocks.append(("tbl", rows))
    return blocks


def parse_pairs(rows: list[list[str]], context_names: list[str]) -> list[dict]:
    """
    כל הטבלאות במקור בנויות באותו דפוס:
        <עמודות הקשר...> | <תווית גמול> | <סכום>
    כשכל רשומה תופסת שתי שורות (ללא גמול / כולל גמול), ותאים חוזרים נשארים ריקים.

    עמודת הקיבוץ הראשונה (למשל 'מחוז') ממוזגת במקור על פני כמה שורות, ולכן
    היא - ורק היא - מושלמת מהשורה הקודמת. עמודות כמו 'הערות' נשארות ריקות
    כשהן ריקות, אחרת הערה של מחוז אחד הייתה נדבקת למחוז הבא.
    """
    entries: list[dict] = []
    carry_first = ""

    for row in rows:
        if len(row) < 2:
            continue
        amount = parse_money(row[-1])
        key = gemul_key(row[-2])
        if amount is None or key is None:
            continue  # שורת כותרת או שורה ריקה

        if key != "no_gemul":
            if entries:
                entries[-1]["gemul_a"] = amount
            continue

        ctx = row[: len(row) - 2]
        ctx = (ctx + [""] * len(context_names))[: len(context_names)]
        if ctx[0]:
            carry_first = ctx[0]
        else:
            ctx[0] = carry_first

        entries.append({**dict(zip(context_names, ctx)), "no_gemul": amount})
    return entries


# --------------------------------------------------------------------------
# מפרט השקופיות בחוברות ה"רפרנטים" (זהה בכל ארבע רמות הוותק)
# --------------------------------------------------------------------------

FIELD_SLIDES = [
    {
        "id": "patrol_detective_kapaz",
        "name": 'סייר / בלש (סיור ובילוש)',
        "framework": 'במסגרת קפ"ז תחנות',
        "salary_group": 6,
        "context": ["activity_level", "rank"],
    },
    {
        "id": "investigator_kapaz",
        "name": "חוקר",
        "framework": 'במסגרת קפ"ז תחנות',
        "salary_group": 5,
        "context": ["activity_level", "rank"],
    },
    {
        "id": "patrol_detective",
        "name": "סייר / בלש (סיור ובילוש)",
        "framework": 'לא במסגרת קפ"ז תחנות',
        "salary_group": 6,
        "context": ["district", "activity_level", "notes"],
    },
    {
        "id": "yasam_patrol",
        "name": 'סייר יס"מ',
        "framework": None,
        "salary_group": 6,
        "context": ["district", "activity_level", "notes"],
    },
    {
        "id": "investigator",
        "name": "חוקר",
        "framework": 'לא במסגרת קפ"ז תחנות',
        "salary_group": 5,
        "context": ["district", "activity_level", "notes"],
    },
    {
        "id": "dispatcher_100",
        "name": "מוקדן 100 / משגר",
        "framework": None,
        "salary_group": 6,
        "context": ["district", "activity_level", "notes"],
    },
    {
        "id": "magav_fighter",
        "name": 'לוחם / סייר מג"ב',
        "framework": None,
        "salary_group": 6,
        "context": ["activity_level", "notes"],
    },
]

FIELD_FILES = {
    0: "סימולציות - מצגת לרפרנטים ללא וותק.pptx",
    1: "סימולציות - מצגת לרפרנטים וותק של שנה 1.pptx",
    2: "סימולציות - מצגת לרפרנטים וותק של שנתיים.pptx",
    3: "מצגת רפרנטים 28.5.25 - שלוש שנים וותק.pptx",
}

MANAGER_FILES = {
    0: "‏‏סימולציות מנהלי  ללא וותק.pptx",
    1: "סימולציות מנהלי שנה וותק.pptx",
    2: "‏‏סימולציות מנהלי שנתיים וותק.pptx",
    2.8: "סימולציות מנהלי שנתיים ושמונה.pptx",
}

LOD_FILE = "סייר יס''מ - תחנת לוד.pptx"
JERUSALEM_FILE = "סימולציות מחוז ירושלים 14.7.2025.docx"


def find_source(name: str) -> Path:
    """איתור קובץ מקור עמיד לתווי כיווניות ורווחים כפולים בשם הקובץ."""
    target = clean(name)
    for p in SRC_DIR.iterdir():
        if clean(p.name) == target:
            return p
    raise FileNotFoundError(f"קובץ מקור חסר: {name}")


# --------------------------------------------------------------------------
# בניית המקטעים
# --------------------------------------------------------------------------


def build_field() -> list[dict]:
    """מקצועות שטח: לכל וריאנט (מקצוע × מחוז × רמת פעילות) סדרת ערכים לפי וותק."""
    variants: dict[tuple, dict] = {}

    for seniority, filename in FIELD_FILES.items():
        slides = pptx_slides(find_source(filename))
        if len(slides) != len(FIELD_SLIDES):
            raise ValueError(f"{filename}: צפויות {len(FIELD_SLIDES)} שקופיות, נמצאו {len(slides)}")

        for spec, slide in zip(FIELD_SLIDES, slides):
            if not slide["tables"]:
                raise ValueError(f"{filename} {slide['slide']}: לא נמצאה טבלה")
            for entry in parse_pairs(slide["tables"][0], spec["context"]):
                key = (
                    spec["id"],
                    entry.get("district") or "כל הארץ",
                    entry.get("activity_level", ""),
                )
                v = variants.setdefault(
                    key,
                    {
                        "profession_id": spec["id"],
                        "profession": spec["name"],
                        "framework": spec["framework"],
                        "salary_group": spec["salary_group"],
                        "district": key[1],
                        "activity_level": key[2],
                        "rank": entry.get("rank", ""),
                        "notes": entry.get("notes", ""),
                        "by_seniority": {},
                    },
                )
                v["by_seniority"][str(seniority)] = {
                    "no_gemul": entry["no_gemul"],
                    "gemul_a": entry.get("gemul_a"),
                }

    out = list(variants.values())
    for v in out:
        missing = {"0", "1", "2", "3"} - set(v["by_seniority"])
        if missing:
            raise ValueError(f"חסרות רמות וותק {sorted(missing)} עבור {v}")
    return out


def build_managers() -> list[dict]:
    """מנהלים: קבוצות שכר 1-8, לפי דירוג, עם סדרת וותק 0 / 1 / 2 / 2.8."""
    variants: dict[tuple, dict] = {}

    for seniority, filename in MANAGER_FILES.items():
        slides = pptx_slides(find_source(filename))
        if len(slides) != 8:
            raise ValueError(f"{filename}: צפויות 8 שקופיות (קבוצות שכר 1-8), נמצאו {len(slides)}")

        for idx, slide in enumerate(slides, start=1):
            entries = parse_pairs(slide["tables"][0], ["rating", "activity_level", "rank"])
            for entry in entries:
                key = (idx, entry["rating"])
                v = variants.setdefault(
                    key,
                    {
                        "salary_group": idx,
                        "rating": entry["rating"],
                        "activity_level": entry["activity_level"],
                        "rank": entry["rank"],
                        "by_seniority": {},
                    },
                )
                v["by_seniority"][str(seniority)] = {
                    "no_gemul": entry["no_gemul"],
                    "gemul_a": entry.get("gemul_a"),
                }

    return sorted(variants.values(), key=lambda v: (v["salary_group"], v["rating"]))


def build_lod() -> list[dict]:
    slide = pptx_slides(find_source(LOD_FILE))[0]
    entries = parse_pairs(slide["tables"][0], ["rifleman_level", "activity_level", "rank"])
    return entries


def build_jerusalem() -> dict:
    """
    מסמך מחוז ירושלים: לכל תפקיד, בתחנה / לא בתחנה, לפי וותק.
    הכותרת שלפני כל טבלה נושאת את שם התפקיד ואת הוותק.
    """
    blocks = docx_blocks(find_source(JERUSALEM_FILE))
    role_re = re.compile(r"^(.+?)\s*[–-]\s*דירוג\s+(\S+)\s*,\s*(\d+)\s+שנים\s+וותק")

    roles: dict[tuple, dict] = {}
    pending = None
    for kind, payload in blocks:
        if kind == "p":
            m = role_re.match(payload)
            if m:
                pending = {
                    "role_raw": clean(m.group(1)),
                    "rating": clean(m.group(2)),
                    "seniority": int(m.group(3)),
                }
        elif kind == "tbl" and pending:
            entries = parse_pairs(payload, ["activity_level", "rank"])
            if entries:
                e = entries[0]
                raw = pending["role_raw"]
                in_station = "לא בתחנה" not in raw and "בתחנה" in raw
                role = re.sub(r"\s*(לא בתחנה|בתחנה)\s*", " ", raw).strip()
                key = (role, in_station)
                r = roles.setdefault(
                    key,
                    {
                        "role": role,
                        "in_station": in_station,
                        "rating": pending["rating"],
                        "activity_level": e["activity_level"],
                        "rank": e["rank"],
                        "by_seniority": {},
                    },
                )
                r["by_seniority"][str(pending["seniority"])] = {
                    "no_gemul": e["no_gemul"],
                    "gemul_a": e.get("gemul_a"),
                }
            pending = None

    # תוספות התחנה כפי שמופיעות בגוף המסמך
    uplift = {"station_dedicated_blm": None, "shekel_blm": None, "total": None}
    for kind, payload in blocks:
        if kind != "p":
            continue
        m = re.search(r"עליה של:?\s*([\d,]+)\s*₪\s*\(\s*([\d,]+)\s*₪.*?\+\s*([\d,]+)\s*₪", payload)
        if m:
            uplift = {
                "total": parse_money(m.group(1)),
                "station_dedicated_blm": parse_money(m.group(2)),
                "shekel_blm": parse_money(m.group(3)),
            }
            break

    return {"roles": sorted(roles.values(), key=lambda r: (r["role"], r["in_station"])), "station_uplift": uplift}


def main() -> int:
    if not SRC_DIR.is_dir():
        print(f"לא נמצאה תיקיית המקור: {SRC_DIR}", file=sys.stderr)
        return 1

    dataset = {
        "meta": {
            "source_dir": SRC_DIR.name,
            "source_files": sorted(clean(p.name) for p in SRC_DIR.iterdir() if p.is_file()),
            "generated_by": "tools/build_dataset.py",
            "field_basis": "כולל הוצאות אישיות",
            "manager_basis": "ללא הוצאות אישיות",
            "lod_basis": "לא כולל הוצאות אישיות",
            "assumptions": [
                "השכלה תיכונית",
                "שירות צבאי (צה\"ל חובה) נלקח בחשבון בסימולציות מחוז ירושלים",
            ],
            "expense_reimbursement": {'רס"ר': 344},
            "gemul_levels": {"no_gemul": GEMUL_NONE, "gemul_a": GEMUL_A},
        },
        "field": build_field(),
        "managers": build_managers(),
        "lod_yasam": build_lod(),
        "jerusalem": build_jerusalem(),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"נכתב: {OUT.relative_to(ROOT)}")
    print(f"  וריאנטים בשטח : {len(dataset['field'])}")
    print(f"  וריאנטים מנהלים: {len(dataset['managers'])}")
    print(f"  שורות לוד      : {len(dataset['lod_yasam'])}")
    print(f"  תפקידי ירושלים : {len(dataset['jerusalem']['roles'])}")
    print(f"  תוספת תחנה     : {dataset['jerusalem']['station_uplift']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
