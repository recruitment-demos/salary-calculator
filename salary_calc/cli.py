# -*- coding: utf-8 -*-
"""
ממשק שורת פקודה לסימולטור השכר.

דוגמאות:
    python3 -m salary_calc.cli --list
    python3 -m salary_calc.cli --profession yasam_patrol --district ירושלים --seniority 2.5
    python3 -m salary_calc.cli --manager-group 6 --rating "אקדמאי ישים 7%" --seniority 1
    python3 -m salary_calc.cli --profession investigator --district ירושלים --station --net
    python3 -m salary_calc.cli --profession magav_fighter --activity "א'" --curve
    python3 -m salary_calc.cli --profession investigator --district ירושלים --json
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import ROUND_HALF_UP, Decimal

from .engine import CalculationError, Dataset, Result, district_label, load_dataset
from .tax import estimate_net, load_params

LINE = "─" * 62


def money(x: float) -> str:
    """
    עיגול חצי-כלפי-מעלה, כדי שהתצוגה תהיה זהה לזו של עמוד הווב.
    ברירת המחדל של פייתון היא עיגול לזוגי הקרוב, ולכן 9,786.5 היה מוצג
    כ-9,786 ב-CLI מול 9,787 בדפדפן - אותו קלט, שני מספרים.
    """
    rounded = Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{rounded:,} ₪"


def print_catalog(ds: Dataset) -> None:
    print(LINE)
    print("מקצועות שטח")
    print(LINE)
    for p in ds.professions():
        fw = f" [{p['framework']}]" if p["framework"] else ""
        print(f"\n  {p['id']}  -  {p['name']}{fw}  (קבוצת שכר {p['salary_group']})")
        for v in ds.variants(p["id"]):
            note = f"  ({v['notes']})" if v["notes"] else ""
            anchors = " / ".join(
                money(v["by_seniority"][k]["no_gemul"]) for k in ("0", "1", "2", "3")
            )
            print(f"      מחוז {v['district']:<8} רמת פעילות {v['activity_level']:<16}{note}")
            print(f"          ותק 0/1/2/3 (ללא גמול): {anchors}")

    print()
    print(LINE)
    print("מנהלים - קבוצות שכר 1-8")
    print(LINE)
    for rating in ds.manager_ratings():
        print(f"\n  דירוג: {rating}")
        for g in ds.manager_groups():
            rec = ds._find_manager(g, rating)
            s = rec["by_seniority"]
            print(
                f"      קבוצה {g}: ותק 0={money(s['0']['no_gemul'])}  "
                f"2.8={money(s['2.8']['no_gemul'])}"
            )

    print()
    print(LINE)
    print('סייר יס"מ - תחנת לוד (ותק 2.8, ללא הוצאות אישיות)')
    print(LINE)
    for row in ds.lod:
        print(
            f"  {row['rifleman_level']:<30} ללא גמול {money(row['no_gemul'])}  "
            f"כולל גמול א' {money(row['gemul_a'])}"
        )
    print()


def print_result(r: Result, net_credit_points: float | None) -> None:
    sel = r.selection
    print()
    print(LINE)
    if sel["track"] == "magav":
        title = f"מג\"ב · {sel['sector']} · {sel['role']} (רמת פעילות {sel['activity_level']})"
    elif sel["track"] == "field":
        title = f"{sel['profession']} · {sel['district']} · רמת פעילות {sel['activity_level']}"
    elif sel["track"] == "manager":
        title = f"מנהלים · קבוצת שכר {sel['salary_group']} · {sel['rating']}"
    else:
        title = f"סייר יס\"מ תחנת לוד · {sel['rifleman_level']}"
    print(title)
    print(LINE)

    print(f"  ותק             : {sel['seniority']} שנים")
    print(f"  גמול השתלמות    : {sel['gemul']}")
    if sel.get("framework"):
        print(f"  מסגרת           : {sel['framework']}")
    if sel.get("rank"):
        print(f"  דרגת שכר        : {sel['rank']}")
    if sel.get("in_station"):
        print(f"  שירות בתחנה     : כן")

    print()
    print("  פירוט החישוב:")
    for c in r.breakdown.components:
        sign = "-" if c.amount < 0 else "+"
        print(f"    {sign} {c.label:<44} {money(abs(c.amount)):>12}")
        if c.note:
            print(f"        {c.note}")

    print()
    print(f"  ברוטו חודשי     : {money(r.monthly_gross)}   ({r.basis})")
    print(f"  ברוטו שנתי      : {money(r.annual_gross)}")

    if net_credit_points is not None:
        t = estimate_net(r.monthly_gross, credit_points=net_credit_points)
        print()
        print(f"  אומדן נטו ({t.year}, {net_credit_points} נק' זיכוי):")
        print(f"    - מס הכנסה                                {money(t.income_tax):>12}")
        print(f"    - ביטוח לאומי                             {money(t.national_insurance):>12}")
        print(f"    - ביטוח בריאות                            {money(t.health_insurance):>12}")
        print(f"    = נטו משוער                               {money(t.net):>12}")
        print(f"      {t.disclaimer}")
        for w in t.warnings:
            print(f"      ⚠  {w}")

    if r.is_estimate:
        print()
        print("  ⚠  התוצאה מבוססת אקסטרפולציה - הערכה, לא נתון מהסימולציה.")
    for w in r.warnings:
        print(f"  ⚠  {w}")
    print()


def print_curve(ds: Dataset, args) -> None:
    print()
    print(LINE)
    print("התקדמות שכר לפי ותק")
    print(LINE)
    years = [0, 0.5, 1, 1.5, 2, 2.5, 3]
    base = None
    for y in years:
        r = ds.calculate_field(
            profession_id=args.profession,
            district=args.district,
            activity_level=args.activity,
            seniority=y,
            gemul=args.gemul,
            in_station=args.station,
        )
        if base is None:
            base = r.monthly_gross
        delta = r.monthly_gross - base
        bar = "█" * int((r.monthly_gross - base) / 8) if delta else ""
        print(f"  ותק {y:>4}  {money(r.monthly_gross):>12}   (+{delta:>5,.0f})  {bar}")
    print()


def print_range(rng, ds: Dataset) -> None:
    print()
    print(LINE)
    print("טווח שכר אפשרי")
    print(LINE)

    if rng.is_single:
        print(f"\n  כל הפרטים ידועים - סכום יחיד: {money(rng.minimum)}")
    else:
        print(f"\n  {money(rng.minimum)}   עד   {money(rng.maximum)}")
        print(f"  רוחב הטווח: {money(rng.spread)}")
    print(f"  בסיס: {rng.basis}")
    print(f"  צירופים אפשריים: {rng.combinations}")

    if rng.unknowns:
        print(f"\n  פרטים שאינם ידועים: {', '.join(rng.unknowns)}")

    print("\n  תרחישי הקצה:")
    print(f"    הנמוך ביותר  {money(rng.low.amount):>12}   {rng.low.describe()}")
    print(f"    הגבוה ביותר  {money(rng.high.amount):>12}   {rng.high.describe()}")

    if not rng.is_single:
        print()
        print("  זהו טווח ולא הערכה: שני הקצוות הם ערכים אמיתיים מהסימולציות.")
        print("  כל פרט נוסף שיימסר יצמצם את הטווח.")
    print()


def print_families(ds: Dataset) -> None:
    print()
    print(LINE)
    print("משפחות מקצוע")
    print(LINE)
    for f in ds.families():
        kapaz = 'יש הבחנת קפ"ז תחנות' if f["has_kapaz"] else 'אין הבחנת קפ"ז'
        print(f"\n  {f['id']:<20} {f['name']}   ({kapaz})")
        for d in ds.districts_for(f["id"]):
            levels = ds.activity_levels_for(f["id"], district=d)
            print(f"      {district_label(d):<12} {', '.join(levels)}")
    print()


def print_magav_matrix(ds: Dataset) -> None:
    """טבלת הכיסוי של שיוך מג"ב: מה אפשר לתמחר, ומה חסר וכמה."""
    print()
    print(LINE)
    print('שיוך מג"ב - מה ניתן לתמחור ומה חסר')
    print(LINE)

    priceable = 0
    empty = 0
    reasons: dict[str, int] = {}

    for sector in ds.magav_sectors():
        print(f"\n  {sector}")
        for role in ds.magav_roles():
            r = ds.resolve_magav(sector, role)
            label = f"    {role:<24}"
            if r["priceable"]:
                amount = ds.calculate_magav(sector, role, 0).monthly_gross
                print(f"{label} {r['level']:<10} {money(amount):>12}")
                priceable += 1
            elif not r["level"]:
                print(f"{label} {'—':<10} (לא קיים במרחב)")
                empty += 1
            else:
                print(f"{label} {r['level']:<10} ⚠ אין טבלת שכר")
                reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1

    print()
    print(LINE)
    print(f"  ניתן לתמחור : {priceable} תאים")
    print(f"  חסר נתון    : {sum(reasons.values())} תאים")
    print(f"  לא רלוונטי  : {empty} תאים (התפקיד אינו קיים במרחב)")
    print(LINE)
    print("\n  סיבות החוסר:")
    for reason, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"\n    [{n} תאים] {reason}")
    print()


def resolve_defaults(ds: Dataset, args) -> None:
    """משלים מחוז ורמת פעילות כשלא צוינו, אם יש רק אפשרות אחת סבירה."""
    variants = ds.variants(args.profession)
    if not args.district:
        districts = ds.districts(args.profession)
        args.district = "כל הארץ" if "כל הארץ" in districts else districts[0]
    if not args.activity:
        levels = ds.activity_levels(args.profession, args.district)
        if not levels:
            raise CalculationError(
                f"אין רמות פעילות עבור {args.profession} במחוז {args.district}"
            )
        args.activity = levels[0]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="salary_calc",
        description="סימולטור שכר - מבוסס טבלאות הסימולציה הרשמיות",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--list", action="store_true", help="הצגת כל המקצועות והאפשרויות")

    rg = p.add_argument_group("טווח (כשלא יודעים את כל הפרטים)")
    rg.add_argument(
        "--range",
        action="store_true",
        help="חישוב טווח: כל פרט שלא צוין נחשב 'לא ידוע'",
    )
    rg.add_argument("--family", help="משפחת מקצוע (patrol_detective / investigator / ...)")
    rg.add_argument(
        "--kapaz",
        choices=["yes", "no"],
        help='מסגרת קפ"ז תחנות. השמטה = לא ידוע',
    )
    rg.add_argument(
        "--families", action="store_true", help="הצגת משפחות המקצוע הזמינות"
    )

    g = p.add_argument_group("מקצועות שטח")
    g.add_argument("--profession", help="מזהה מקצוע (ראה --list)")
    g.add_argument("--district", help="מחוז")
    g.add_argument("--activity", help="רמת פעילות")
    g.add_argument("--station", action="store_true", help='שירות בתחנה (בל"מ ייעודי תחנות)')

    m = p.add_argument_group("מנהלים")
    m.add_argument("--manager-group", type=int, choices=range(1, 9), help="קבוצת שכר 1-8")
    m.add_argument("--rating", help="דירוג שכר")

    g2 = p.add_argument_group('מג"ב לפי מרחב')
    g2.add_argument("--sector", help='מרחב מג"ב')
    g2.add_argument("--role", help="תפקיד בטבלת השיוך")
    g2.add_argument(
        "--magav-matrix",
        action="store_true",
        help="טבלת כיסוי מלאה: מה ניתן לתמחור ומה חסר",
    )

    l = p.add_argument_group('תחנת לוד')
    l.add_argument("--lod", action="store_true", help='סייר יס"מ תחנת לוד')
    l.add_argument("--rifleman", action="store_true", help="רובאי 05")

    c = p.add_argument_group("כללי")
    c.add_argument("--seniority", type=float, default=0.0, help="ותק בשנים (ברירת מחדל 0)")
    c.add_argument(
        "--gemul",
        choices=["no_gemul", "gemul_a"],
        default="no_gemul",
        help="גמול השתלמות",
    )
    c.add_argument(
        "--no-expenses",
        action="store_true",
        help="הצגה ללא הוצאות אישיות (מקצועות שטח)",
    )
    c.add_argument("--net", nargs="?", type=float, const=2.25, help="אומדן נטו (נק' זיכוי, ברירת מחדל 2.25)")
    c.add_argument("--curve", action="store_true", help="טבלת התקדמות לפי ותק")
    c.add_argument("--json", action="store_true", help="פלט JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    args.seniority_given = any(a == "--seniority" or a.startswith("--seniority=") for a in raw)
    args.gemul_given = any(a == "--gemul" or a.startswith("--gemul=") for a in raw)
    ds = load_dataset()

    try:
        if args.families:
            print_families(ds)
            return 0

        if args.range:
            rng = ds.calculate_range(
                family_id=args.family,
                kapaz={"yes": True, "no": False}.get(args.kapaz),
                district=args.district,
                activity_level=args.activity,
                seniority=args.seniority if args.seniority_given else None,
                gemul=args.gemul if args.gemul_given else None,
                in_station=True if args.station else None,
                include_personal_expenses=not args.no_expenses,
            )
            if args.json:
                print(json.dumps(rng.as_dict(), ensure_ascii=False, indent=2))
            else:
                print_range(rng, ds)
            return 0

        if args.magav_matrix:
            print_magav_matrix(ds)
            return 0

        if args.sector or args.role:
            if not (args.sector and args.role):
                raise CalculationError("נדרשים גם --sector וגם --role")
            result = ds.calculate_magav(
                sector=args.sector,
                role=args.role,
                seniority=args.seniority,
                gemul=args.gemul,
            )
            if args.json:
                print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
            else:
                print_result(result, args.net)
            return 0

        if args.list or not (args.profession or args.manager_group or args.lod):
            print_catalog(ds)
            if not (args.profession or args.manager_group or args.lod):
                print("בחר מקצוע כדי לחשב, לדוגמה:")
                print('  python3 -m salary_calc.cli --profession yasam_patrol --district ירושלים --seniority 2')
            return 0

        if args.lod:
            result = ds.calculate_lod(rifleman=args.rifleman, gemul=args.gemul)
        elif args.manager_group:
            rating = args.rating or ds.manager_ratings()[0]
            result = ds.calculate_manager(
                salary_group=args.manager_group,
                rating=rating,
                seniority=args.seniority,
                gemul=args.gemul,
            )
        else:
            resolve_defaults(ds, args)
            if args.curve:
                print_curve(ds, args)
                return 0
            result = ds.calculate_field(
                profession_id=args.profession,
                district=args.district,
                activity_level=args.activity,
                seniority=args.seniority,
                gemul=args.gemul,
                in_station=args.station,
                include_personal_expenses=not args.no_expenses,
            )

        if args.json:
            out = result.as_dict()
            if args.net is not None:
                out["net_estimate"] = estimate_net(
                    result.monthly_gross, credit_points=args.net
                ).as_dict()
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print_result(result, args.net)
        return 0

    except CalculationError as e:
        print(f"\nשגיאה: {e}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
