// Shared date/time formatting, so every page shows times the same way.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// '2026-11-03T09:42:00' -> 'Nov 3, 9:42 AM'
export function formatTime(iso) {
  const [date, time] = iso.split('T');
  const [, m, d] = date.split('-').map(Number);
  let [hh, mm] = time.split(':').map(Number);
  const ampm = hh >= 12 ? 'PM' : 'AM';
  hh = hh % 12 || 12;
  return `${MONTHS[m - 1]} ${d}, ${hh}:${String(mm).padStart(2, '0')} ${ampm}`;
}

// '2026-10-21' or '2026-10-21T10:00:00' -> 'Oct 21'
export function formatDay(iso) {
  const [, m, d] = iso.slice(0, 10).split('-').map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

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
