/*
 * בדיקת התאמה בין מנוע ה-JS שבעמוד לבין מנוע הפייתון.
 *
 * הסקריפט שולף את פונקציות החישוב מתוך index.html (אותו קוד בדיוק
 * שרץ בדפדפן), מריץ אותן על מטריצת קלטים, ומדפיס JSON להשוואה מול
 * salary_calc/engine.py. ההשוואה עצמה נעשית ב-tests/test_parity.py.
 *
 * הרצה עצמאית:  node tests/parity_check.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

// חילוץ גוש הסקריפט של העמוד
const m = HTML.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('לא נמצא גוש script ב-index.html'); process.exit(2); }

// הקוד שמתחת להערה "מצב ותפריטים" נוגע ב-DOM; לוקחים רק את שכבת החישוב.
const cut = m[1].indexOf('/* ---------- מצב ותפריטים');
if (cut < 0) { console.error('לא נמצא הגבול של שכבת החישוב'); process.exit(2); }
const code = m[1].slice(0, cut);

const sandbox = { Intl, Math, Object, Number, Error, JSON, console };
vm.createContext(sandbox);
vm.runInContext(code + '\nthis.__api = {calcField, calcManager, calcLod, calcMagav, resolveMagav, calculateRange, JOB_FAMILIES, estimateNet, interpolate, DATA, MAGAV};', sandbox);

const { calcField, calcManager, calcLod, calcMagav, resolveMagav, calculateRange,
        JOB_FAMILIES, estimateNet, DATA, MAGAV } = sandbox.__api;

const SENIORITIES = [0, 0.5, 1, 1.75, 2, 2.5, 3, 4.25];
const GEMULS = ['no_gemul', 'gemul_a'];
const out = [];

// ------- מקצועות שטח: כל וריאנט × ותק × גמול × תחנה × הוצאות
for (const v of DATA.field) {
  for (const gemul of GEMULS) {
    for (const seniority of SENIORITIES) {
      for (const station of [false, true]) {
        for (const noExpenses of [false, true]) {
          const r = calcField({
            profession: v.profession_id, district: v.district, activity: v.activity_level,
            seniority, gemul, station, noExpenses,
          });
          out.push({
            kind: 'field', profession: v.profession_id, district: v.district,
            activity: v.activity_level, seniority, gemul, station, noExpenses,
            total: +r.total.toFixed(6), basis: r.basis, estimated: r.estimated,
          });
        }
      }
    }
  }
}

// ------- מנהלים
for (const mg of DATA.managers) {
  for (const gemul of GEMULS) {
    for (const seniority of SENIORITIES) {
      const r = calcManager({ group: mg.salary_group, rating: mg.rating, seniority, gemul });
      out.push({
        kind: 'manager', group: mg.salary_group, rating: mg.rating, seniority, gemul,
        total: +r.total.toFixed(6), basis: r.basis, estimated: r.estimated,
      });
    }
  }
}

// ------- תחנת לוד
DATA.lod_yasam.forEach((_, index) => {
  for (const gemul of GEMULS) {
    const r = calcLod({ index, gemul });
    out.push({ kind: 'lod', index, gemul, total: +r.total.toFixed(6), basis: r.basis });
  }
});

// ------- שיוך מג"ב: כל מרחב × כל תפקיד, גם התאים שאי אפשר לתמחר
for (const row of MAGAV.rows) {
  for (const role of MAGAV.roles) {
    const res = resolveMagav(row.sector, role);
    const rec = {
      kind: 'magav', sector: row.sector, role,
      level: res.level || '', priceable: !!res.priceable,
    };
    if (res.priceable) {
      for (const seniority of [0, 1.5, 3]) {
        out.push({ ...rec, seniority, total: +calcMagav({ sector: row.sector, role, seniority, gemul: 'no_gemul' }).total.toFixed(6) });
      }
    } else {
      out.push(rec);
    }
  }
}

// ------- טווחים: כל שילוב של "מה ידוע" ו"מה לא"
const FAMILY_OPTS = [null, ...JOB_FAMILIES.map(f => f.id)];
for (const familyId of FAMILY_OPTS) {
  for (const kapaz of [null, true, false]) {
    for (const district of [null, 'ירושלים', 'כל הארץ']) {
      for (const seniority of [null, 0, 2.5]) {
        for (const gemul of [null, 'no_gemul']) {
          for (const station of [null, false, true]) {
            let r;
            try {
              r = calculateRange({ familyId, kapaz, district, activity: null, seniority, gemul, station, noExpenses: false });
            } catch (e) {
              out.push({ kind: 'range', familyId, kapaz, district, seniority, gemul, station, error: true });
              continue;
            }
            out.push({
              kind: 'range', familyId, kapaz, district, seniority, gemul, station,
              error: false,
              minimum: +r.minimum.toFixed(6), maximum: +r.maximum.toFixed(6),
              combinations: r.combinations, isSingle: r.isSingle,
              unknowns: r.unknowns,
            });
          }
        }
      }
    }
  }
}

// ------- אומדן נטו
for (const gross of [9000, 11500, 13368, 17000, 25000, 60000]) {
  for (const cp of [0, 2.25, 2.75]) {
    const t = estimateNet(gross, cp);
    out.push({
      kind: 'net', gross, credit_points: cp,
      income_tax: +t.incomeTax.toFixed(6), ni: +t.ni.toFixed(6),
      health: +t.health.toFixed(6), net: +t.net.toFixed(6),
    });
  }
}

process.stdout.write(JSON.stringify(out));
