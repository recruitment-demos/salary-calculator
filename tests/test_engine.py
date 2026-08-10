# -*- coding: utf-8 -*-
"""
בדיקות המנוע.

הבדיקה המרכזית: לכל שורה בכל טבלת מקור, המנוע חייב להחזיר בדיוק את הסכום
שמופיע בטבלה - אחרת הסימולטור "ממציא" מספרים.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from salary_calc.engine import CalculationError, interpolate, load_dataset
from salary_calc.tax import estimate_net, load_params

DS = load_dataset()
GEMULS = ("no_gemul", "gemul_a")


class TestAnchorFidelity(unittest.TestCase):
    """בכל נקודת עוגן התוצאה חייבת להיות זהה למקור, ללא סטייה."""

    def test_field_anchors_match_source(self):
        checked = 0
        for v in DS.field:
            for gemul in GEMULS:
                for sen_str, vals in v["by_seniority"].items():
                    expected = vals.get(gemul)
                    if expected is None:
                        continue
                    r = DS.calculate_field(
                        profession_id=v["profession_id"],
                        district=v["district"],
                        activity_level=v["activity_level"],
                        seniority=float(sen_str),
                        gemul=gemul,
                    )
                    self.assertAlmostEqual(
                        r.monthly_gross,
                        expected,
                        places=6,
                        msg=f"{v['profession_id']}/{v['district']}/{v['activity_level']} "
                        f"ותק {sen_str} {gemul}",
                    )
                    self.assertFalse(r.is_estimate)
                    checked += 1
        self.assertGreater(checked, 190, "נבדקו פחות שורות מהצפוי")

    def test_manager_anchors_match_source(self):
        checked = 0
        for m in DS.managers:
            for gemul in GEMULS:
                for sen_str, vals in m["by_seniority"].items():
                    expected = vals.get(gemul)
                    if expected is None:
                        continue
                    r = DS.calculate_manager(
                        salary_group=m["salary_group"],
                        rating=m["rating"],
                        seniority=float(sen_str),
                        gemul=gemul,
                    )
                    self.assertAlmostEqual(r.monthly_gross, expected, places=6)
                    checked += 1
        self.assertEqual(checked, 24 * 2 * 4)

    def test_lod_matches_source(self):
        for row in DS.lod:
            rifleman = row["rifleman_level"].startswith("רובאי")
            for gemul in GEMULS:
                r = DS.calculate_lod(rifleman=rifleman, gemul=gemul)
                self.assertEqual(r.monthly_gross, row[gemul])


class TestStationUplift(unittest.TestCase):
    """תוספת התחנה חייבת לשחזר את מסמך מחוז ירושלים בדיוק."""

    def test_uplift_reproduces_jerusalem_document(self):
        roles = {(r["role"], r["in_station"]): r for r in DS.jerusalem["roles"]}
        uplift = DS.station_uplift["station_dedicated_blm"]
        pairs = [("חוקר", "חוקר")]
        for base_role, station_role in pairs:
            out_of = roles[(base_role, False)]
            in_st = roles[(station_role, True)]
            for sen, vals in out_of["by_seniority"].items():
                self.assertEqual(
                    vals["no_gemul"] + uplift,
                    in_st["by_seniority"][sen]["no_gemul"],
                    msg=f"{base_role} ותק {sen}: תוספת התחנה אינה {uplift}",
                )

    def test_engine_applies_uplift(self):
        kw = dict(
            profession_id="investigator",
            district="ירושלים",
            activity_level="שיטור חדש",
            seniority=3,
        )
        base = DS.calculate_field(**kw).monthly_gross
        with_station = DS.calculate_field(**kw, in_station=True).monthly_gross
        self.assertEqual(with_station - base, DS.station_uplift["station_dedicated_blm"])
        # מול המסמך: חוקר בתחנה, 3 שנים = 11,675 ₪
        self.assertEqual(with_station, 11675)

    def test_patrol_station_matches_document(self):
        # בלש לא בתחנה 3 שנים = 11,610 ; סייר/בלש בתחנה = 12,320
        r = DS.calculate_field(
            profession_id="patrol_detective",
            district="ירושלים",
            activity_level="שיטור חדש",
            seniority=3,
            in_station=True,
        )
        self.assertEqual(r.monthly_gross, 12320)


class TestInterpolation(unittest.TestCase):
    def test_exact_at_anchors(self):
        s = {0.0: 100.0, 1.0: 110.0, 2.0: 130.0}
        for x, expected in s.items():
            val, est = interpolate(s, x)
            self.assertEqual(val, expected)
            self.assertFalse(est)

    def test_midpoint(self):
        val, est = interpolate({0.0: 100.0, 2.0: 200.0}, 1.0)
        self.assertEqual(val, 150.0)
        self.assertFalse(est)

    def test_extrapolation_flagged(self):
        val, est = interpolate({0.0: 100.0, 1.0: 110.0}, 3.0)
        self.assertEqual(val, 130.0)
        self.assertTrue(est)

    def test_half_year_between_anchors(self):
        r = DS.calculate_field(
            profession_id="patrol_detective_kapaz",
            district="כל הארץ",
            activity_level="שיטור חדש",
            seniority=0.5,
        )
        # בין 11,312 ל-11,399
        self.assertAlmostEqual(r.monthly_gross, (11312 + 11399) / 2)
        self.assertFalse(r.is_estimate)

    def test_beyond_range_is_estimate(self):
        r = DS.calculate_field(
            profession_id="patrol_detective_kapaz",
            district="כל הארץ",
            activity_level="שיטור חדש",
            seniority=10,
        )
        self.assertTrue(r.is_estimate)
        self.assertTrue(r.warnings)


class TestValidation(unittest.TestCase):
    def test_unknown_profession(self):
        with self.assertRaises(CalculationError):
            DS.calculate_field("nope", "כל הארץ", "ב'", 0)

    def test_invalid_combination_lists_options(self):
        with self.assertRaises(CalculationError) as ctx:
            DS.calculate_field("dispatcher_100", "דרום", "ג'", 0)
        self.assertIn("קיים:", str(ctx.exception))

    def test_negative_seniority(self):
        with self.assertRaises(CalculationError):
            DS.calculate_field("magav_fighter", "כל הארץ", "ב'", -1)

    def test_unknown_manager_rating(self):
        with self.assertRaises(CalculationError):
            DS.calculate_manager(1, "לא קיים", 0)


class TestExpenses(unittest.TestCase):
    def test_deducts_documented_reimbursement(self):
        kw = dict(
            profession_id="patrol_detective_kapaz",
            district="כל הארץ",
            activity_level="ב'",
            seniority=0,
        )
        incl = DS.calculate_field(**kw)
        excl = DS.calculate_field(**kw, include_personal_expenses=False)
        self.assertEqual(incl.monthly_gross - excl.monthly_gross, 344)
        self.assertEqual(excl.basis, "ללא הוצאות אישיות")

    def test_no_deduction_when_rank_undocumented(self):
        # רשומות ללא דרגת רס"ר מפורשת - אין ניכוי, ומוצגת אזהרה
        r = DS.calculate_field(
            profession_id="magav_fighter",
            district="כל הארץ",
            activity_level="ב'",
            seniority=0,
            include_personal_expenses=False,
        )
        self.assertEqual(r.basis, "כולל הוצאות אישיות")
        self.assertTrue(r.warnings)


class TestResultShape(unittest.TestCase):
    def test_annual_and_components(self):
        r = DS.calculate_field("magav_fighter", "כל הארץ", "א'", 2)
        self.assertAlmostEqual(r.annual_gross, r.monthly_gross * 12)
        d = r.as_dict()
        self.assertEqual(
            round(sum(c["amount"] for c in d["components"]), 2), d["monthly_gross"]
        )

    def test_navigation_helpers(self):
        self.assertEqual(len(DS.professions()), 7)
        self.assertIn("ירושלים", DS.districts("yasam_patrol"))
        self.assertEqual(DS.manager_groups(), list(range(1, 9)))
        self.assertEqual(len(DS.manager_ratings()), 3)


class TestRanges(unittest.TestCase):
    """טווחים כשחלק מהפרטים אינם ידועים."""

    def test_all_unknown_spans_whole_dataset(self):
        r = DS.calculate_range()
        every = [
            v["by_seniority"][s][g]
            for v in DS.field
            for s in v["by_seniority"]
            for g in ("no_gemul", "gemul_a")
            if v["by_seniority"][s][g] is not None
        ]
        self.assertEqual(r.minimum, min(every))
        # המקסימום יכול לכלול תוספת תחנה בירושלים, ולכן לא פחות מהמרבי הגולמי
        self.assertGreaterEqual(r.maximum, max(every))
        self.assertFalse(r.is_single)

    def test_bounds_are_real_values_not_averages(self):
        """כל קצה חייב להיות ערך שאפשר להגיע אליו בחישוב רגיל."""
        r = DS.calculate_range(family_id="magav_fighter")
        for sc in (r.low, r.high):
            direct = DS.calculate_field(
                profession_id=sc.profession_id,
                district=sc.district,
                activity_level=sc.activity_level,
                seniority=sc.seniority,
                gemul=sc.gemul,
                in_station=sc.in_station,
            )
            self.assertAlmostEqual(direct.monthly_gross, sc.amount, places=6)

    def test_more_information_narrows_range(self):
        wide = DS.calculate_range(family_id="patrol_detective")
        narrow = DS.calculate_range(family_id="patrol_detective", district="ירושלים")
        narrower = DS.calculate_range(
            family_id="patrol_detective", district="ירושלים", activity_level="שיטור חדש"
        )
        self.assertGreater(wide.spread, narrow.spread)
        self.assertGreaterEqual(narrow.spread, narrower.spread)
        self.assertGreater(wide.combinations, narrow.combinations)

    def test_full_information_collapses_to_single(self):
        r = DS.calculate_range(
            family_id="investigator",
            kapaz=False,
            district="ירושלים",
            activity_level="שיטור חדש",
            seniority=2,
            gemul="no_gemul",
            in_station=False,
        )
        self.assertTrue(r.is_single)
        self.assertEqual(r.combinations, 1)
        self.assertEqual(r.unknowns, [])
        self.assertEqual(r.minimum, 10874)

    def test_range_matches_jerusalem_document(self):
        """הקצה העליון בירושלים חייב להיות בדיוק הערך שבמסמך המחוז."""
        r = DS.calculate_range(family_id="patrol_detective", kapaz=False, district="ירושלים")
        self.assertEqual(r.minimum, 11322)  # ותק 0, ללא גמול, לא בתחנה
        self.assertEqual(r.maximum, 14181)  # ותק 3, כולל גמול, בתחנה

    def test_station_not_assumed_outside_jerusalem(self):
        """תוספת התחנה מתועדת בירושלים בלבד ואין להניח אותה במחוזות אחרים."""
        r = DS.calculate_range(family_id="magav_fighter")
        self.assertFalse(r.high.in_station)
        every = [
            v["by_seniority"][s]["gemul_a"]
            for v in DS.field
            if v["profession_id"] == "magav_fighter"
            for s in v["by_seniority"]
        ]
        self.assertEqual(r.maximum, max(every))

    def test_kapaz_filter(self):
        both = DS.calculate_range(family_id="investigator")
        only_kapaz = DS.calculate_range(family_id="investigator", kapaz=True)
        self.assertLess(only_kapaz.combinations, both.combinations)
        self.assertNotIn('מסגרת קפ"ז תחנות', only_kapaz.unknowns)

    def test_family_without_kapaz_has_no_kapaz_unknown(self):
        r = DS.calculate_range(family_id="magav_fighter")
        self.assertNotIn('מסגרת קפ"ז תחנות', r.unknowns)

    def test_kapaz_true_for_family_without_kapaz_fails(self):
        with self.assertRaises(CalculationError):
            DS.calculate_range(family_id="magav_fighter", kapaz=True)

    def test_unknown_family_rejected(self):
        with self.assertRaises(CalculationError):
            DS.calculate_range(family_id="nope")

    def test_district_labels(self):
        from salary_calc.engine import district_label

        self.assertEqual(district_label("כל הארץ"), "שאר הארץ")
        self.assertEqual(district_label("ירושלים"), "ירושלים")
        self.assertEqual(district_label('מתפ"א'), 'מתפ"א')  # יחידה עצמאית - נשארת בשמה

    def test_navigation_helpers_for_families(self):
        self.assertEqual(len(DS.families()), 5)
        self.assertIn("ירושלים", DS.districts_for("patrol_detective"))
        self.assertTrue(DS.activity_levels_for("magav_fighter"))


class TestMagavAssignment(unittest.TestCase):
    """טבלת השיוך של מג"ב: מרחב × תפקיד -> רמת פעילות -> שכר (או פער מפורש)."""

    def test_matrix_shape(self):
        self.assertEqual(len(DS.magav_sectors()), 13)
        self.assertEqual(len(DS.magav_roles()), 6)

    def test_fighter_is_priceable_everywhere_it_appears(self):
        role = next(r for r in DS.magav_roles() if r.startswith('לוחם מג"ב'))
        priced = 0
        for sector in DS.magav_sectors():
            res = DS.resolve_magav(sector, role)
            if not res["level"]:
                continue  # מטה - התא ריק
            self.assertTrue(res["priceable"], msg=f"{sector}: {res.get('reason')}")
            priced += 1
        self.assertEqual(priced, 12)

    def test_level_normalization(self):
        # במסמך השיוך כתוב 'א+', ובטבלת השכר "א' +" - אותה רמה
        res = DS.resolve_magav('ימ"מ', next(r for r in DS.magav_roles() if r.startswith('לוחם מג"ב')))
        self.assertEqual(res["level"], "א+")
        self.assertEqual(res["activity_level"], "א' +")

    def test_matches_magav_salary_table(self):
        role = next(r for r in DS.magav_roles() if r.startswith('לוחם מג"ב'))
        via_magav = DS.calculate_magav('ימ"מ', role, seniority=0).monthly_gross
        direct = DS.calculate_field("magav_fighter", "כל הארץ", "א' +", 0).monthly_gross
        self.assertEqual(via_magav, direct)

    def test_group_7_reported_as_gap_not_guessed(self):
        role = next(r for r in DS.magav_roles() if r.startswith('לוחם ימ"ס'))
        res = DS.resolve_magav("דרום", role)
        self.assertFalse(res["priceable"])
        self.assertIn("קבוצה 7", res["reason"])
        with self.assertRaises(CalculationError):
            DS.calculate_magav("דרום", role, 0)

    def test_see_other_doc_reported(self):
        res = DS.resolve_magav('איו"ש', "חוקר")
        self.assertFalse(res["priceable"])
        self.assertIn("דף מקצוע", res["reason"])

    def test_empty_cell_reported(self):
        role = next(r for r in DS.magav_roles() if r.startswith('לוחם מג"ב'))
        res = DS.resolve_magav("מטה", role)
        self.assertFalse(res["priceable"])
        self.assertIsNone(res["level"])

    def test_unknown_inputs(self):
        with self.assertRaises(CalculationError):
            DS.resolve_magav("מרחב לא קיים", DS.magav_roles()[0])
        with self.assertRaises(CalculationError):
            DS.resolve_magav("דרום", "תפקיד לא קיים")

    def test_every_cell_is_either_priced_or_explained(self):
        """אין תא שמחזיר 'לא ניתן' בלי סיבה - זה מה שהופך את הפערים לשימושיים."""
        for sector in DS.magav_sectors():
            for role in DS.magav_roles():
                res = DS.resolve_magav(sector, role)
                if res["priceable"]:
                    self.assertIn("activity_level", res)
                else:
                    self.assertTrue(res["reason"].strip(), msg=f"{sector}/{role}")


class TestDisplayRounding(unittest.TestCase):
    """
    התצוגה ב-CLI חייבת לעגל כמו Math.round של JS (חצי כלפי מעלה).
    אחרת אותו קלט מציג מספר שונה בטרמינל ובדפדפן.
    """

    def test_half_up_not_bankers(self):
        from salary_calc.cli import money

        self.assertEqual(money(9786.5), "9,787 ₪")
        self.assertEqual(money(9785.5), "9,786 ₪")  # פייתון היה מציג 9,786 גם כאן
        self.assertEqual(money(11312), "11,312 ₪")

    def test_matches_engine_value(self):
        from salary_calc.cli import money

        r = DS.calculate_manager(6, "אקדמאי ישים 7%", 1.5)
        self.assertEqual(r.monthly_gross, 9786.5)
        self.assertEqual(money(r.monthly_gross), "9,787 ₪")


class TestTax(unittest.TestCase):
    PARAMS = load_params()

    def test_net_below_gross(self):
        r = estimate_net(12000, credit_points=2.25, params=self.PARAMS)
        self.assertLess(r.net, 12000)
        self.assertGreater(r.net, 0)

    def test_deductions_sum(self):
        r = estimate_net(15000, params=self.PARAMS).as_dict()
        self.assertAlmostEqual(
            r["gross"] - r["total_deductions"], r["net"], places=2
        )

    def test_progressive_monotonic(self):
        nets = [estimate_net(g, params=self.PARAMS).net for g in range(8000, 30000, 1000)]
        self.assertEqual(nets, sorted(nets), "נטו חייב לעלות עם הברוטו")

    def test_more_credit_points_more_net(self):
        a = estimate_net(14000, credit_points=2.25, params=self.PARAMS).net
        b = estimate_net(14000, credit_points=3.25, params=self.PARAMS).net
        self.assertAlmostEqual(b - a, self.PARAMS["credit_point_value"], places=2)

    def test_rejects_non_positive(self):
        with self.assertRaises(ValueError):
            estimate_net(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
