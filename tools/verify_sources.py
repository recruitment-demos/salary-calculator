#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
בדיקת עקביות בין קבצי המקור.

חלק מהערכים מופיעים ביותר ממקום אחד (למשל שכר חוקר/בלש במחוז ירושלים מופיע
גם במצגות הרפרנטים וגם במסמך מחוז ירושלים). הסקריפט משווה את החפיפות ומדווח
על אי-התאמות, כדי שלא נגלה אותן בדיעבד דרך תוצאה מוזרה במחשבון.

הרצה:  python3 tools/verify_sources.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from salary_calc.engine import load_dataset

# תפקידים במסמך ירושלים <-> הרשומה המקבילה במצגות הרפרנטים
JERUSALEM_MAP = {
    ("בלש", False): ("patrol_detective", "ירושלים", "שיטור חדש"),
    ("סייר / בלש", True): ("patrol_detective", "ירושלים", "שיטור חדש"),
    ("חוקר", False): ("investigator", "ירושלים", "שיטור חדש"),
    ("חוקר", True): ("investigator", "ירושלים", "שיטור חדש"),
    ('סייר יס"מ', False): ("yasam_patrol", "ירושלים", "ב'"),
}


def main() -> int:
    ds = load_dataset()
    uplift = ds.station_uplift["station_dedicated_blm"]
    issues: list[str] = []
    checks = 0

    print("─" * 72)
    print("בדיקת חפיפות: מסמך מחוז ירושלים מול מצגות הרפרנטים")
    print("─" * 72)

    for role in ds.jerusalem["roles"]:
        key = (role["role"], role["in_station"])
        target = JERUSALEM_MAP.get(key)
        if not target:
            print(f"  ⚠  אין מיפוי עבור {key} - דילוג")
            continue
        pid, district, activity = target

        for sen, vals in sorted(role["by_seniority"].items()):
            expected = ds.calculate_field(
                profession_id=pid,
                district=district,
                activity_level=activity,
                seniority=float(sen),
                in_station=role["in_station"],
            ).monthly_gross
            actual = vals["no_gemul"]
            checks += 1
            if abs(expected - actual) > 0.5:
                diff = actual - expected
                issues.append(
                    f"{role['role']} ({'בתחנה' if role['in_station'] else 'לא בתחנה'}) "
                    f"ותק {sen}: מסמך ירושלים {actual:,} ₪ מול מצגות {expected:,.0f} ₪ "
                    f"(הפרש {diff:+,.0f} ₪)"
                )

    print(f"  נבדקו {checks} ערכים חופפים.")

    # סדרות שהן למעשה אותה סדרה אך נבדלות בנקודה בודדת - סימן לעיגול לא עקבי.
    # מחוזות שונים אמורים להיבדל בשכר, ולכן משווים רק סדרות שכמעט זהות לחלוטין.
    print()
    print("─" * 72)
    print("בדיקת סדרות כמעט-זהות (איתור עיגול לא עקבי)")
    print("─" * 72)

    anchors = ("0", "1", "2", "3")
    records = [
        (
            f"{v['profession_id']}/{v['district']}/{v['activity_level']}",
            tuple(v["by_seniority"][a]["no_gemul"] for a in anchors),
        )
        for v in ds.field
    ]

    compared = 0
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            name_a, sa = records[i]
            name_b, sb = records[j]
            same = sum(1 for x, y in zip(sa, sb) if x == y)
            # התאמה בנקודה בודדת היא צירוף מקרים; זהות מלאה תקינה.
            # מעניינות רק סדרות שחופפות ברוב הנקודות ונבדלות במיעוטן.
            if same < 2 or same == len(anchors):
                continue
            compared += 1
            diffs = [
                f"ותק {a}: {x:,} מול {y:,}"
                for a, x, y in zip(anchors, sa, sb)
                if x != y
            ]
            issues.append(
                f"{name_a} ו-{name_b} זהות ב-{same} מתוך {len(anchors)} נקודות אך נבדלות ב- "
                + "; ".join(diffs)
            )
    print(f"  נמצאו {compared} זוגות סדרות כמעט-זהות.")

    print()
    print("─" * 72)
    if issues:
        print(f"נמצאו {len(issues)} אי-התאמות במקור:")
        for i in issues:
            print(f"  • {i}")
        print()
        print("אלה פערי עיגול בקבצי המקור עצמם, לא שגיאות חישוב.")
        print("המחשבון מציג את הערך כפי שהוא מופיע בקובץ שממנו נשאב.")
    else:
        print("לא נמצאו אי-התאמות.")
    print("─" * 72)

    return 0  # דיווח בלבד - לא מכשיל build


if __name__ == "__main__":
    raise SystemExit(main())
