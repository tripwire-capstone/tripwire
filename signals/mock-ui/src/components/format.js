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

// '2026-10-21' -> 'Oct 21'
export function formatDay(isoDate) {
  const [, m, d] = isoDate.split('-').map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}
