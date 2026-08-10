# -*- coding: utf-8 -*-
"""
מנוע החישוב של סימולטור השכר.

עקרונות:
  * כל נקודות העוגן מגיעות מטבלאות הסימולציה הרשמיות (data/salary_data.json).
  * ותק שאינו נקודת עוגן מחושב באינטרפולציה לינארית בין העוגנים הסמוכים;
    מעבר לוותק המרבי שבנתונים מתבצעת אקסטרפולציה - והתוצאה מסומנת כהערכה.
  * תוספות מתועדות (בל"מ ייעודי תחנות, בל"מ שקלית, החזר הוצאות אישיות)
    מיושמות כרכיבים נפרדים, כך שהפירוט תמיד מראה מאיפה הגיע כל שקל.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Iterable, Literal

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "salary_data.json"

GemulLevel = Literal["no_gemul", "gemul_a"]
Track = Literal["field", "manager", "lod"]

GEMUL_LABELS = {"no_gemul": "ללא גמול השתלמות", "gemul_a": "כולל גמול השתלמות א'"}

# החזר הוצאות אישיות ברוטו, כפי שמופיע במצגת תחנת לוד.
# מתועד עבור דרגת שכר רס"ר בלבד - לא מבצעים המרה לדרגות אחרות.
EXPENSE_REIMBURSEMENT = {'רס"ר': 344}


class CalculationError(ValueError):
    """קלט שאינו קיים בנתוני הסימולציה."""


# --------------------------------------------------------------------------
# מבני תוצאה
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Component:
    """רכיב בודד בפירוט החישוב."""

    label: str
    amount: float
    note: str = ""

    def as_dict(self) -> dict:
        return {"label": self.label, "amount": round(self.amount, 2), "note": self.note}


@dataclass
class Breakdown:
    components: list[Component] = dc_field(default_factory=list)

    def add(self, label: str, amount: float, note: str = "") -> None:
        self.components.append(Component(label, amount, note))

    @property
    def total(self) -> float:
        return sum(c.amount for c in self.components)


@dataclass
class Result:
    """תוצאת חישוב מלאה, כולל פירוט והנחות."""

    monthly_gross: float
    breakdown: Breakdown
    basis: str
    selection: dict
    anchors: dict
    is_estimate: bool = False
    warnings: list[str] = dc_field(default_factory=list)

    @property
    def annual_gross(self) -> float:
        return self.monthly_gross * 12

    def as_dict(self) -> dict:
        return {
            "monthly_gross": round(self.monthly_gross, 2),
            "annual_gross": round(self.annual_gross, 2),
            "basis": self.basis,
            "is_estimate": self.is_estimate,
            "selection": self.selection,
            "anchors": self.anchors,
            "warnings": list(self.warnings),
            "components": [c.as_dict() for c in self.breakdown.components],
        }


# --------------------------------------------------------------------------
# אינטרפולציה על ותק
# --------------------------------------------------------------------------


def interpolate(series: dict[float, float], x: float) -> tuple[float, bool]:
    """
    מחזיר (ערך, האם_הערכה) עבור ותק x לפי סדרת עוגנים {ותק: סכום}.

    בין עוגנים - אינטרפולציה לינארית (מדויקת בנקודות העוגן עצמן).
    מעבר לעוגן האחרון - אקסטרפולציה לפי שיפוע המקטע האחרון, מסומנת כהערכה.
    """
    if not series:
        raise CalculationError("אין נתוני ותק עבור הבחירה")
    xs = sorted(series)
    if len(xs) == 1:
        return series[xs[0]], x != xs[0]
    if x <= xs[0]:
        return series[xs[0]], x < xs[0]
    if x >= xs[-1]:
        if x == xs[-1]:
            return series[xs[-1]], False
        x0, x1 = xs[-2], xs[-1]
        slope = (series[x1] - series[x0]) / (x1 - x0)
        return series[x1] + slope * (x - x1), True
    for a, b in zip(xs, xs[1:]):
        if a <= x <= b:
            t = (x - a) / (b - a)
            return series[a] + t * (series[b] - series[a]), False
    raise CalculationError(f"ותק מחוץ לטווח: {x}")  # pragma: no cover


# --------------------------------------------------------------------------
# מאגר הנתונים
# --------------------------------------------------------------------------


class Dataset:
    """גישה לנתוני הסימולציה ולחישוב עצמו."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.meta = raw["meta"]
        self.field = raw["field"]
        self.managers = raw["managers"]
        self.lod = raw["lod_yasam"]
        self.jerusalem = raw["jerusalem"]
        self.station_uplift = self.jerusalem["station_uplift"]

    # ---------------------------------------------------------------- ניווט

    def professions(self) -> list[dict]:
        """רשימת המקצועות בשטח, ללא כפילויות, בסדר הופעתם."""
        seen: dict[str, dict] = {}
        for v in self.field:
            seen.setdefault(
                v["profession_id"],
                {
                    "id": v["profession_id"],
                    "name": v["profession"],
                    "framework": v["framework"],
                    "salary_group": v["salary_group"],
                },
            )
        return list(seen.values())

    def variants(self, profession_id: str) -> list[dict]:
        found = [v for v in self.field if v["profession_id"] == profession_id]
        if not found:
            raise CalculationError(f"מקצוע לא קיים: {profession_id}")
        return found

    def districts(self, profession_id: str) -> list[str]:
        return sorted({v["district"] for v in self.variants(profession_id)})

    def activity_levels(self, profession_id: str, district: str) -> list[str]:
        return [v["activity_level"] for v in self.variants(profession_id) if v["district"] == district]

    def manager_groups(self) -> list[int]:
        return sorted({m["salary_group"] for m in self.managers})

    def manager_ratings(self) -> list[str]:
        seen: list[str] = []
        for m in self.managers:
            if m["rating"] not in seen:
                seen.append(m["rating"])
        return seen

    # ------------------------------------------------------------- חיפוש רשומה

    def _find_field(self, profession_id: str, district: str, activity_level: str) -> dict:
        for v in self.variants(profession_id):
            if v["district"] == district and v["activity_level"] == activity_level:
                return v
        available = [
            f"{v['district']} / {v['activity_level']}" for v in self.variants(profession_id)
        ]
        raise CalculationError(
            f"אין סימולציה עבור {profession_id} במחוז '{district}' ברמת פעילות '{activity_level}'.\n"
            f"קיים: {', '.join(available)}"
        )

    def _find_manager(self, salary_group: int, rating: str) -> dict:
        for m in self.managers:
            if m["salary_group"] == salary_group and m["rating"] == rating:
                return m
        raise CalculationError(
            f"אין סימולציה עבור קבוצת שכר {salary_group} בדירוג '{rating}'.\n"
            f"דירוגים קיימים: {', '.join(self.manager_ratings())}"
        )

    @staticmethod
    def _series(record: dict, gemul: GemulLevel) -> dict[float, float]:
        series = {}
        for k, v in record["by_seniority"].items():
            amount = v.get(gemul)
            if amount is not None:
                series[float(k)] = float(amount)
        if not series:
            raise CalculationError(f"אין נתונים עבור {GEMUL_LABELS[gemul]}")
        return series

    # -------------------------------------------------------------- חישובים

    def calculate_field(
        self,
        profession_id: str,
        district: str,
        activity_level: str,
        seniority: float,
        gemul: GemulLevel = "no_gemul",
        in_station: bool = False,
        include_personal_expenses: bool = True,
    ) -> Result:
        """
        חישוב שכר למקצוע שטח.

        נתוני הבסיס כוללים הוצאות אישיות. שירות בתחנה מוסיף בל"מ ייעודי תחנות
        (הפרש שנמדד מנתוני מחוז ירושלים ומתועד במסמך המקור).
        """
        if seniority < 0:
            raise CalculationError("ותק לא יכול להיות שלילי")

        record = self._find_field(profession_id, district, activity_level)
        series = self._series(record, gemul)
        base, estimated = interpolate(series, seniority)

        bd = Breakdown()
        anchor_note = (
            f"עוגני ותק: {', '.join(f'{int(k) if k == int(k) else k}={int(v):,}' for k, v in sorted(series.items()))}"
        )
        bd.add(
            f"בסיס - {record['profession']} ({activity_level}, {district})",
            base,
            anchor_note,
        )

        warnings: list[str] = []
        if estimated:
            warnings.append(
                f"ותק {seniority} חורג מהעוגן המרבי בנתונים ({max(series)}) - הערך חושב באקסטרפולציה ומהווה הערכה."
            )

        if in_station:
            uplift = self.station_uplift["station_dedicated_blm"]
            bd.add(
                'בל"מ ייעודי תחנות (שירות בתחנה)',
                uplift,
                f"תוספת מתועדת של {uplift:,} ₪ לשוטרי תחנה",
            )
            if district != "ירושלים":
                warnings.append(
                    'תוספת בל"מ ייעודי תחנות מתועדת בנתוני מחוז ירושלים בלבד; '
                    "היישום למחוזות אחרים הוא הנחה."
                )

        basis = "כולל הוצאות אישיות"
        if not include_personal_expenses:
            rank_key = self._rank_family(record.get("rank") or record.get("notes") or "")
            amount = EXPENSE_REIMBURSEMENT.get(rank_key)
            if amount is None:
                warnings.append(
                    "החזר ההוצאות האישיות מתועד עבור דרגת רס\"ר בלבד; "
                    "לא ניתן לנכות אותו עבור הרשומה הזו - התוצאה נותרה כוללת הוצאות."
                )
            else:
                bd.add("ניכוי החזר הוצאות אישיות", -amount, f'מתועד לדרגת רס"ר')
                basis = "ללא הוצאות אישיות"

        return Result(
            monthly_gross=bd.total,
            breakdown=bd,
            basis=basis,
            selection={
                "track": "field",
                "profession": record["profession"],
                "profession_id": profession_id,
                "framework": record["framework"],
                "salary_group": record["salary_group"],
                "district": district,
                "activity_level": activity_level,
                "rank": record["rank"],
                "notes": record["notes"],
                "seniority": seniority,
                "gemul": GEMUL_LABELS[gemul],
                "in_station": in_station,
            },
            anchors={str(k): v for k, v in sorted(series.items())},
            is_estimate=estimated,
            warnings=warnings,
        )

    def calculate_manager(
        self,
        salary_group: int,
        rating: str,
        seniority: float,
        gemul: GemulLevel = "no_gemul",
    ) -> Result:
        """חישוב שכר למנהלים לפי קבוצת שכר ודירוג. הנתונים ללא הוצאות אישיות."""
        if seniority < 0:
            raise CalculationError("ותק לא יכול להיות שלילי")

        record = self._find_manager(salary_group, rating)
        series = self._series(record, gemul)
        base, estimated = interpolate(series, seniority)

        bd = Breakdown()
        bd.add(
            f"בסיס - מנהלים, קבוצת שכר {salary_group} ({rating})",
            base,
            f"רמת פעילות {record['activity_level']}, דרגת שכר {record['rank']}",
        )

        warnings = []
        if estimated:
            warnings.append(
                f"ותק {seniority} חורג מהעוגן המרבי בנתונים ({max(series)}) - הערך חושב באקסטרפולציה ומהווה הערכה."
            )

        return Result(
            monthly_gross=bd.total,
            breakdown=bd,
            basis="ללא הוצאות אישיות",
            selection={
                "track": "manager",
                "salary_group": salary_group,
                "rating": rating,
                "activity_level": record["activity_level"],
                "rank": record["rank"],
                "seniority": seniority,
                "gemul": GEMUL_LABELS[gemul],
            },
            anchors={str(k): v for k, v in sorted(series.items())},
            is_estimate=estimated,
            warnings=warnings,
        )

    def calculate_lod(self, rifleman: bool, gemul: GemulLevel = "no_gemul") -> Result:
        """סייר יס\"מ - תחנת לוד. נתון נקודתי לוותק 2.8 שנים, ללא הוצאות אישיות."""
        wanted = "רובאי 05" if rifleman else "ללא רובאי 05"
        for row in self.lod:
            if row["rifleman_level"].startswith(wanted):
                amount = row.get(gemul)
                if amount is None:
                    raise CalculationError(f"אין נתון עבור {GEMUL_LABELS[gemul]}")
                bd = Breakdown()
                bd.add(
                    f'סייר יס"מ תחנת לוד - {row["rifleman_level"]}',
                    amount,
                    f"רמת פעילות {row['activity_level']}, דרגת שכר {row['rank']}",
                )
                return Result(
                    monthly_gross=bd.total,
                    breakdown=bd,
                    basis="לא כולל הוצאות אישיות",
                    selection={
                        "track": "lod",
                        "rifleman_level": row["rifleman_level"],
                        "activity_level": row["activity_level"],
                        "rank": row["rank"],
                        "seniority": 2.8,
                        "gemul": GEMUL_LABELS[gemul],
                    },
                    anchors={"2.8": float(amount)},
                    warnings=["נתוני תחנת לוד תקפים לוותק 2.8 שנים בלבד ולתחנה זו בלבד."],
                )
        raise CalculationError(f"לא נמצאה רשומת לוד עבור '{wanted}'")

    @staticmethod
    def _rank_family(rank_text: str) -> str:
        """מחלץ את משפחת הדרגה ('רס\"ר' / 'רס\"ל') מטקסט חופשי כמו 'דרגת שכר רס\"ר 0'."""
        for family in ('רס"ר', 'רס"ל'):
            if family in rank_text:
                return family
        return ""

    # ------------------------------------------------------------- השוואות

    def compare_seniority(self, years: Iterable[float], **kwargs) -> list[Result]:
        """אותה בחירה על פני מספר רמות ותק - שימושי לגרף התקדמות."""
        return [self.calculate_field(seniority=y, **kwargs) for y in years]


def load_dataset(path: Path | str = DATA_PATH) -> Dataset:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"קובץ הנתונים חסר: {path}\nהרץ תחילה: python3 tools/build_dataset.py"
        )
    return Dataset(json.loads(path.read_text(encoding="utf-8")))
