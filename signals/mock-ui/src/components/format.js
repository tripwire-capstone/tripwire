// Shared date/time formatting, so every page shows times the same way.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Pages keep times in UTC without a zone ('2026-11-03T17:42:00'). Everything shown on screen is in the
// browser's time zone: these helpers do the conversion, so a page never formats a time by hand.
export const toDate = (iso) => new Date(/Z|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);

// '2026-11-03T17:42:00' (UTC) -> 'Nov 3, 9:42 AM' in Los Angeles
export function formatTime(iso) {
  const d = toDate(iso);
  const hh = d.getHours();
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${hh % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')} ${hh >= 12 ? 'PM' : 'AM'}`;
}

// '2026-10-21T10:00:00' (UTC) -> 'Oct 21' (the local day). A bare date '2026-10-21' is shown as is.
export function formatDay(iso) {
  if (iso.length === 10) {
    const [, m, d] = iso.split('-').map(Number);
    return `${MONTHS[m - 1]} ${d}`;
  }
  const d = toDate(iso);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

// The browser's time zone: its IANA name for the API ('America/Los_Angeles'), and its short name for
// the screen ('PST', or 'PDT' in summer: pass the time it's for).
export const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
export const zoneLabel = (iso) =>
  new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' })
    .formatToParts(iso ? toDate(iso) : new Date())
    .find((part) => part.type === 'timeZoneName')?.value ?? '';

// plural(1, 'event') -> '1 event'; plural(3, 'event') -> '3 events'
export function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

// Time since `iso`, measured from `now` (the story's current time): 'just now', '12m ago', '5h ago', '5d ago', '2mo ago'.
export function formatRelative(iso, now) {
  const minutes = Math.round((new Date(now) - new Date(iso)) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

// Three significant digits, so costs line up: 0.000948 -> '$0.000948'; 0.22 -> '$0.220'; 12.345 -> '$12.35'
export function formatCost(dollars) {
  if (dollars >= 1) return `$${dollars.toFixed(2)}`;
  if (dollars === 0) return '$0';
  return `$${dollars.toPrecision(3)}`;
}

// Shorten a long id for a table cell: '55e4f63847b211b7...' -> '55e4f638...'
export const shortId = (id, n = 8) => (id && id.length > n ? `${id.slice(0, n)}…` : id);
