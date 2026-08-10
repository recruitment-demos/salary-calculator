# -*- coding: utf-8 -*-
"""
אומדן נטו - מודול רשות ונפרד.

חשוב: הנתונים בתיקיית המקור הם נתוני *ברוטו* בלבד. המודול הזה אינו מבוסס
עליהם אלא על מדרגות מס הכנסה וביטוח לאומי הכלליות, ולכן הוא אומדן גס:

  * לשוטרים ולאנשי כוחות הביטחון יש הסדרי ביטוח לאומי, פנסיה תקציבית
    וניכויים ייעודיים שאינם מיוצגים כאן.
  * אין כאן זיכויים אישיים (משמרות, פריפריה, ילדים, הפקדות וכו') מעבר
    לנקודות הזיכוי שמזינים ידנית.

הפרמטרים יושבים ב-config/tax_params.json כדי שיהיה אפשר לעדכן אותם
בלי לגעת בקוד. אל תציג את התוצאה כנטו רשמי.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path

PARAMS_PATH = Path(__file__).resolve().parent.parent / "config" / "tax_params.json"


@dataclass
class TaxResult:
    gross: float
    income_tax: float
    national_insurance: float
    health_insurance: float
    credit_points_value: float
    net: float
    year: int
    disclaimer: str
    warnings: list[str] = dc_field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "gross": round(self.gross, 2),
            "income_tax": round(self.income_tax, 2),
            "national_insurance": round(self.national_insurance, 2),
            "health_insurance": round(self.health_insurance, 2),
            "credit_points_value": round(self.credit_points_value, 2),
            "total_deductions": round(
                self.income_tax + self.national_insurance + self.health_insurance, 2
            ),
            "net": round(self.net, 2),
            "year": self.year,
            "disclaimer": self.disclaimer,
            "warnings": list(self.warnings),
        }


def load_params(path: Path | str = PARAMS_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _progressive(amount: float, brackets: list[dict]) -> float:
    """מס פרוגרסיבי: כל מדרגה מוגדרת {'up_to': גבול או null, 'rate': שיעור}."""
    tax = 0.0
    prev = 0.0
    for b in brackets:
        cap = b["up_to"] if b["up_to"] is not None else float("inf")
        if amount <= prev:
            break
        taxable = min(amount, cap) - prev
        tax += taxable * b["rate"]
        prev = cap
    return tax


def estimate_net(
    monthly_gross: float,
    credit_points: float = 2.25,
    params: dict | None = None,
) -> TaxResult:
    """
    אומדן נטו חודשי מברוטו חודשי.

    credit_points: נקודות זיכוי. ברירת המחדל 2.25 היא הערך הנפוץ לגבר תושב
    ישראל (2) עם תוספת נפוצה; נשים זכאיות ל-2.75. יש לעדכן לפי המקרה.
    """
    p = params or load_params()

    warnings: list[str] = []
    if monthly_gross <= 0:
        raise ValueError("ברוטו חייב להיות חיובי")

    # מס הכנסה, בניכוי שווי נקודות זיכוי (לא יורד מתחת לאפס)
    gross_tax = _progressive(monthly_gross, p["income_tax_brackets"])
    credit_value = credit_points * p["credit_point_value"]
    income_tax = max(0.0, gross_tax - credit_value)

    # ביטוח לאומי ובריאות: שיעור מופחת עד הסף, מלא מעליו ועד התקרה
    threshold = p["ni_reduced_threshold"]
    ceiling = p["ni_ceiling"]
    capped = min(monthly_gross, ceiling)
    if monthly_gross > ceiling:
        warnings.append(f"הברוטו עולה על תקרת הביטוח הלאומי ({ceiling:,.0f} ₪) - החישוב נעצר בתקרה.")

    low = min(capped, threshold)
    high = max(0.0, capped - threshold)

    ni = low * p["ni_rate_reduced"] + high * p["ni_rate_full"]
    health = low * p["health_rate_reduced"] + high * p["health_rate_full"]

    net = monthly_gross - income_tax - ni - health

    return TaxResult(
        gross=monthly_gross,
        income_tax=income_tax,
        national_insurance=ni,
        health_insurance=health,
        credit_points_value=credit_value,
        net=net,
        year=p["year"],
        disclaimer=p["disclaimer"],
        warnings=warnings,
    )
