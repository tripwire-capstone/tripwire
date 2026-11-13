// Time ranges, like Laminar's picker: a relative range ("past 7 days") or an absolute one (start..end).
// The range lives in the page address so links keep it:
//   ?pastHours=168                    relative to now (the story's current time)
//   ?startDate=<iso>&endDate=<iso>    absolute
// Each page picks its own default (Signal detail: 3 days; lists: 7 days, like Laminar).

export const DEFAULT_PAST_HOURS = 168;

export const PRESETS = [
  { label: '1 hour', hours: 1 },
  { label: '3 hours', hours: 3 },
  { label: '1 day', hours: 24 },
  { label: '3 days', hours: 72 },
  { label: '1 week', hours: 168 },
  { label: '1 month', hours: 720 },
  { label: '3 months', hours: 2160 },
  { label: '6 months', hours: 4320 },
];

const pad = (n) => String(n).padStart(2, '0');
const toIso = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
const fromIso = (iso) => new Date(`${iso}Z`);
export const addHours = (iso, hours) => toIso(new Date(fromIso(iso).getTime() + hours * 3_600_000));

// "4 hours", "8d", "2 weeks", "30 min", "3 months" -> hours (or null if it can't be read).
export function parseDuration(text) {
  const m = String(text).trim().toLowerCase().match(/^(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|wk|weeks?|mo|months?)$/);
  if (!m) return null;
  const n = Number(m[1]);
  const unit = m[2];
  if (unit.startsWith('mo')) return n * 720;
  if (unit === 'm' || unit.startsWith('min')) return n / 60;
  if (unit.startsWith('h')) return n;
  if (unit.startsWith('d')) return n * 24;
  if (unit.startsWith('w')) return n * 168;
  return null;
}

// 168 -> '7d', 4 -> '4h', 0.5 -> '30m', 720 -> '30d'
export function shortLabel(hours) {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24 || hours % 24 !== 0) return `${Math.round(hours * 10) / 10}h`;
  return `${hours / 24}d`;
}

// Read the range from URL search params. Returns { start, end, hours, label, absolute }.
export function rangeFromParams(params, now, defaultHours = DEFAULT_PAST_HOURS) {
  const startDate = params.get('startDate');
  const endDate = params.get('endDate');
  if (startDate && endDate) {
    const hours = (fromIso(endDate) - fromIso(startDate)) / 3_600_000;
    return { start: startDate, end: endDate, hours, label: 'Custom', absolute: true };
  }
  const pastHours = Number(params.get('pastHours')) || defaultHours;
  return { start: addHours(now, -pastHours), end: now, hours: pastHours, label: shortLabel(pastHours), absolute: false };
}

// Write a range into URL search params (keeps the other params).
export function withRange(params, range) {
  const next = new URLSearchParams(params);
  next.delete('pastHours'); next.delete('startDate'); next.delete('endDate'); next.delete('pageNumber');
  if (range.absolute) { next.set('startDate', range.start); next.set('endDate', range.end); } else next.set('pastHours', String(range.hours));
  return next;
}

// Bar size for a range: small bars for short ranges, daily bars for long ones (~60-180 bars).
export function bucketMinutesFor(hours) {
  if (hours <= 1) return 1;
  if (hours <= 3) return 2;
  if (hours <= 24) return 15;
  if (hours <= 24 * 7) return 60;
  if (hours <= 24 * 31) return 360;
  if (hours <= 24 * 93) return 1440;
  return 2880;
}

// Split [start, end] into buckets: [{ start, end }] in the pages' format (UTC, no zone), each `minutes`
// long on the LOCAL clock: hour bars start on the local hour, day bars at local midnight, so a bar
// labeled "Thu 12" is the browser's Thursday. Steps are wall-clock steps, so a daylight-saving day is
// one 23- or 25-hour bar. The API groups its counts the same way (it gets the zone as `tz`):
// sub-day sizes divide the day from local midnight; multi-day sizes count days from 2000-01-01.
export function makeBuckets(start, end, minutes) {
  const s = fromIso(start);
  const last = fromIso(end).getTime();
  let t;
  if (minutes >= 1440) {
    const days = minutes / 1440;
    const dayNumber = Math.round((Date.UTC(s.getFullYear(), s.getMonth(), s.getDate()) - Date.UTC(2000, 0, 1)) / 86_400_000);
    t = new Date(2000, 0, 1 + dayNumber - (((dayNumber % days) + days) % days));
  } else {
    const sinceMidnight = s.getHours() * 60 + s.getMinutes();
    t = new Date(s.getFullYear(), s.getMonth(), s.getDate(), 0, Math.floor(sinceMidnight / minutes) * minutes);
  }
  const out = [];
  while (t.getTime() <= last) {
    const next = new Date(t.getFullYear(), t.getMonth(), t.getDate(), t.getHours(), t.getMinutes() + minutes);
    out.push({ start: toIso(t), end: toIso(next) });
    t = next;
  }
  return out;
}

// Which bucket an ISO time falls in (index), or -1. Buckets can differ in length (daylight saving),
// so this searches instead of dividing.
export function bucketIndex(buckets, iso) {
  let lo = 0;
  let hi = buckets.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (iso < buckets[mid].start) hi = mid - 1;
    else if (iso >= buckets[mid].end) lo = mid + 1;
    else return mid;
  }
  return -1;
}
