// Dev-source reference, duplicated into each deployable script (see README.md).
// Ported unchanged from V10.5.5 - confirmed "stays as-is" in ARCHITECTURE.md Phase 0 review.
// Public-holiday calculation is pure date math (fixed dates + Easter algorithm) -
// no external calendar/API dependency, matches the "no external data sources" constraint.

// SYNC-BLOCK-START: dates.ts
function isoMonday(year: number, week: number): Date {
  let d = new Date(year, 0, 4);
  let day = d.getDay();
  if (day == 0) {
    day = 7;
  }
  d.setDate(d.getDate() - day + 1 + (week - 1) * 7);
  return d;
}

function easter(y: number): Date {
  const f = Math.floor;
  let a = y % 19;
  let b = f(y / 100);
  let c = y % 100;
  let d = f(b / 4);
  let e = b % 4;
  let g = f((8 * b + 13) / 25);
  let h = (19 * a + b - d - g + 15) % 30;
  let i = f(c / 4);
  let k = c % 4;
  let l = (32 + 2 * e + 2 * i - h - k) % 7;
  let m = f((a + 11 * h + 22 * l) / 451);
  let month = f((h + l - 7 * m + 114) / 31);
  let day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(y, month - 1, day);
}

// Czech public holidays: 11 fixed dates + Good Friday + Easter Monday.
function isHoliday(date: Date, year: number): boolean {
  const fixed = [
    "1-1", "1-5", "8-5", "5-7", "6-7", "28-9",
    "28-10", "17-11", "24-12", "25-12", "26-12",
  ];
  const key = date.getDate() + "-" + (date.getMonth() + 1);
  if (fixed.includes(key)) {
    return true;
  }
  const e = easter(year);
  let friday = new Date(e);
  friday.setDate(e.getDate() - 2);
  let monday = new Date(e);
  monday.setDate(e.getDate() + 1);
  return (
    date.toDateString() == friday.toDateString() ||
    date.toDateString() == monday.toDateString()
  );
}

// Returns the working (non-holiday) Mon-Fri days for a given ISO week.
// This is the automatic part of dynamic capacity - CAPACITY_OVERRIDE (a
// new V11 config table) can still override the resulting day/visit count
// manually; see docs/BUSINESS_RULES.md section 8.
function workDays(year: number, week: number): { day: string; date: Date }[] {
  const names = ["MON", "TUE", "WED", "THU", "FRI"];
  let start = isoMonday(year, week);
  let result: { day: string; date: Date }[] = [];
  for (let i = 0; i < 5; i++) {
    let d = new Date(start);
    d.setDate(start.getDate() + i);
    if (!isHoliday(d, year)) {
      result.push({ day: names[i], date: d });
    }
  }
  return result;
}
// SYNC-BLOCK-END: dates.ts
