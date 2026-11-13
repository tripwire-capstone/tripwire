
// Links between screens. Use these instead of writing URLs by hand, so every page links the same way.
//
// Tripwire detail reads these optional query parameters:
//   ?tab=events|runs|settings  which tab is open (default events)
//   ?cluster=<clusterId>       the selected cluster (filters the chart and the events table)
//   ?event=<eventId>           an event to open in the side panel
//   ?pastHours=168 or ?startDate=&endDate=   the time range (see timeRange.js)
// Read them on the detail page with: const [params] = useSearchParams(); params.get('cluster')

export const homeUrl = () => '/';
export const tripwireListUrl = () => '/signals';
export const newTripwireUrl = () => '/signals/new';

// `range` (optional): a range from rangeFromParams(), so the signal page opens on the same time range.
export function tripwireUrl(tripwireId, { tab, clusterId, eventId, range } = {}) {
  const params = new URLSearchParams();
  if (tab && tab !== 'events') params.set('tab', tab);
  if (clusterId) params.set('cluster', clusterId);
  if (eventId) params.set('event', eventId);
  if (range?.absolute) { params.set('startDate', range.start); params.set('endDate', range.end); }
  else if (range) params.set('pastHours', String(range.hours));
  const query = params.toString();
  return `/signals/${tripwireId}${query ? `?${query}` : ''}`;
}


