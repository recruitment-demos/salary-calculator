# -*- coding: utf-8 -*-
"""
בדיקת התאמה: מנוע ה-JS שבעמוד חייב להחזיר בדיוק את מה שמחזיר מנוע הפייתון.

בלי הבדיקה הזו קל מאוד שהעמוד והספרייה יתפצלו בשקט ויציגו מספרים שונים
לאותה בחירה. אם Node אינו זמין - הבדיקה מדלגת ולא נכשלת.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from salary_calc.engine import load_dataset
from salary_calc.tax import estimate_net, load_params

WINDOWS_NODE = Path("/mnt/c/Program Files/nodejs/node.exe")


def find_node() -> tuple[str, bool] | None:
    """מחזיר (נתיב_לריצה, האם_זה_node_של_windows) או None."""
    for candidate in ("node", "nodejs"):
        found = shutil.which(candidate)
        if found:
            return found, False
    if WINDOWS_NODE.exists():
        return str(WINDOWS_NODE), True
    return None


def to_arg_path(path: Path, windows: bool) -> str:
    """node.exe של Windows אינו מבין נתיבי /mnt/c - ממירים דרך wslpath."""
    if not windows:
        return str(path)
    try:
        return subprocess.run(
            ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return str(path)


class TestJsPythonParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        found = find_node()
        if not found:
            raise unittest.SkipTest("Node אינו זמין - בדיקת ההתאמה מדולגת")
        cls.node, is_windows = found

        index = ROOT / "index.html"
        if not index.exists():
            raise unittest.SkipTest("index.html חסר - הרץ python3 tools/build_web.py")

        proc = subprocess.run(
            [cls.node, to_arg_path(ROOT / "tests" / "parity_check.js", is_windows)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        if proc.returncode != 0:
            raise AssertionError(f"parity_check.js נכשל:\n{proc.stderr}")
        cls.js_rows = json.loads(proc.stdout)
        cls.ds = load_dataset()
        cls.tax_params = load_params()

    def test_dataset_covered(self):
        kinds = {r["kind"] for r in self.js_rows}
        self.assertEqual(kinds, {"field", "manager", "lod", "magav", "net"})
        self.assertGreater(len(self.js_rows), 1500)

    def test_magav_parity(self):
        """גם ההחלטה 'אי אפשר לתמחר' חייבת להיות זהה בשני המנועים."""
        rows = [r for r in self.js_rows if r["kind"] == "magav"]
        self.assertTrue(rows)
        for r in rows:
            res = self.ds.resolve_magav(r["sector"], r["role"])
            ctx = f"{r['sector']} / {r['role']}"
            self.assertEqual(bool(res["priceable"]), r["priceable"], msg=ctx)
            self.assertEqual(res["level"] or "", r["level"], msg=ctx)
            if r["priceable"]:
                py = self.ds.calculate_magav(
                    sector=r["sector"], role=r["role"], seniority=r["seniority"]
                )
                self.assertAlmostEqual(py.monthly_gross, r["total"], places=4, msg=ctx)

    def test_field_parity(self):
        rows = [r for r in self.js_rows if r["kind"] == "field"]
        self.assertTrue(rows)
        for r in rows:
            py = self.ds.calculate_field(
                profession_id=r["profession"],
                district=r["district"],
                activity_level=r["activity"],
                seniority=r["seniority"],
                gemul=r["gemul"],
                in_station=r["station"],
                include_personal_expenses=not r["noExpenses"],
            )
            ctx = (
                f"{r['profession']}/{r['district']}/{r['activity']} ותק={r['seniority']} "
                f"{r['gemul']} station={r['station']} noExp={r['noExpenses']}"
            )
            self.assertAlmostEqual(py.monthly_gross, r["total"], places=4, msg=ctx)
            self.assertEqual(py.basis, r["basis"], msg=ctx)
            self.assertEqual(py.is_estimate, r["estimated"], msg=ctx)

    def test_manager_parity(self):
        rows = [r for r in self.js_rows if r["kind"] == "manager"]
        self.assertTrue(rows)
        for r in rows:
            py = self.ds.calculate_manager(
                salary_group=r["group"],
                rating=r["rating"],
                seniority=r["seniority"],
                gemul=r["gemul"],
            )
            ctx = f"קבוצה {r['group']} {r['rating']} ותק={r['seniority']} {r['gemul']}"
            self.assertAlmostEqual(py.monthly_gross, r["total"], places=4, msg=ctx)
            self.assertEqual(py.is_estimate, r["estimated"], msg=ctx)

    def test_lod_parity(self):
        rows = [r for r in self.js_rows if r["kind"] == "lod"]
        self.assertTrue(rows)
        for r in rows:
            rifleman = self.ds.lod[r["index"]]["rifleman_level"].startswith("רובאי")
            py = self.ds.calculate_lod(rifleman=rifleman, gemul=r["gemul"])
            self.assertAlmostEqual(py.monthly_gross, r["total"], places=4)
            self.assertEqual(py.basis, r["basis"])

    def test_net_parity(self):
        rows = [r for r in self.js_rows if r["kind"] == "net"]
        self.assertTrue(rows)
        for r in rows:
            py = estimate_net(r["gross"], credit_points=r["credit_points"], params=self.tax_params)
            ctx = f"ברוטו {r['gross']} נק' {r['credit_points']}"
            self.assertAlmostEqual(py.income_tax, r["income_tax"], places=4, msg=ctx)
            self.assertAlmostEqual(py.national_insurance, r["ni"], places=4, msg=ctx)
            self.assertAlmostEqual(py.health_insurance, r["health"], places=4, msg=ctx)
            self.assertAlmostEqual(py.net, r["net"], places=4, msg=ctx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
