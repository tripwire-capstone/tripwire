// Notification presentation is derived from the same story-day snapshot as every page.
export function withNotificationData(base, readIds = [], actions = {}) {
  const read = new Set(readIds);
  const notifications = base.notifications.map((n) => ({ ...n, read: n.read || read.has(n.id) }));
  const severityOf = (n) => n.type === 'new-cluster' ? 'info' : (base.getEvent(n.eventId)?.severity || 'critical');
  const filteredNotifications = ({ severity = 'all', unreadOnly = false } = {}) => notifications.filter((n) =>
    (!unreadOnly || !n.read) && (severity === 'all' || severityOf(n) === severity));
  const notificationCounts = ({ unreadOnly = false } = {}) => {
    const counts = { all: 0, critical: 0, warning: 0, info: 0 };
    filteredNotifications({ unreadOnly }).forEach((n) => { counts.all += 1; counts[severityOf(n)] += 1; });
    return counts;
  };
  const notificationDetails = (n) => {
    const tripwire = base.getTripwire(n.tripwireId);
    const cluster = base.getCluster(n.clusterId);
    const event = base.getEvent(n.eventId);
    const isCluster = n.type === 'new-cluster';
    const events = cluster ? base.eventsInCluster(cluster.id) : [];
    const times = events.map((e) => e.time).sort();
    const fields = Object.entries(event?.payload || {}).map(([key, value]) => ({
      label: key.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, (c) => c.toUpperCase()),
      value: typeof value === 'string' ? value : JSON.stringify(value),
    }));
    return { tripwire, cluster, event, isCluster, severity: severityOf(n), fields,
      summary: isCluster ? cluster?.summary : n.text,
      eventCount: events.length, firstSeen: times[0], lastSeen: times.at(-1) };
  };
  return { ...base, ...actions, notifications, filteredNotifications, notificationCounts, notificationDetails,
    unreadCount: () => notifications.filter((n) => !n.read).length };
}

export function readNotificationIds() {
  try {
    const ids = JSON.parse(sessionStorage.getItem('notificationReadIds') || '[]');
    return Array.isArray(ids) ? ids.filter((id) => typeof id === 'string') : [];
  } catch { return []; }
}

export function saveNotificationIds(ids) {
  try { sessionStorage.setItem('notificationReadIds', JSON.stringify(ids)); } catch { /* Keep in memory if storage is unavailable. */ }
}
