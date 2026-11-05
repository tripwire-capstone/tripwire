import test from 'node:test';
import assert from 'node:assert/strict';
import { makeStoryData, STORY_DAYS } from '../src/mock/data.js';
import { withNotificationData, readNotificationIds, saveNotificationIds } from '../src/components/notificationData.js';

test('filters, counts, and cluster information follow every story-day boundary', () => {
  for (const day of STORY_DAYS) {
    const base = makeStoryData(day.asOf);
    const data = withNotificationData(base);
    assert.equal(data.notificationCounts().all, base.notifications.length);
    assert.equal(data.notificationCounts().warning, 0);
    for (const n of data.notifications) {
      assert.ok(n.time <= day.asOf);
      const details = data.notificationDetails(n);
      if (details.lastSeen) assert.ok(details.lastSeen <= day.asOf);
      if (details.isCluster) assert.equal(details.eventCount, base.eventsInCluster(n.clusterId).length);
    }
    assert.ok(data.filteredNotifications({ severity: 'critical' }).every((n) => n.type === 'critical-event'));
    assert.equal(data.notificationCounts({ unreadOnly: true }).all, data.unreadCount());
  }
});

test('manual reads combine with automatic reads without modifying the underlying snapshot', () => {
  const base = makeStoryData(STORY_DAYS[0].asOf);
  const n = base.notifications.find((item) => !item.read);
  assert.ok(n);
  const before = withNotificationData(base);
  const after = withNotificationData(base, [n.id]);
  assert.equal(after.unreadCount(), before.unreadCount() - 1);
  assert.equal(n.read, false);
  assert.ok(!after.filteredNotifications({ unreadOnly: true }).some((item) => item.id === n.id));
  assert.equal(withNotificationData(base, base.notifications.map((item) => item.id)).unreadCount(), 0);
});

test('output values are retained and missing linked records are tolerated', () => {
  const data = withNotificationData(makeStoryData());
  const event = data.notifications.find((n) => n.type === 'critical-event');
  assert.deepEqual(data.notificationDetails(event).fields.map((f) => f.value), Object.values(data.getEvent(event.eventId).payload));
  const missing = data.notificationDetails({ type: 'new-cluster', clusterId: 'missing' });
  assert.equal(missing.eventCount, 0);
  assert.equal(missing.firstSeen, undefined);
  assert.deepEqual(data.notificationDetails({ type: 'critical-event' }).fields, []);
  const custom = withNotificationData({ ...makeStoryData(), getEvent: () => ({ payload: { user_signal: 'x'.repeat(2000), detail: { ok: true } } }) });
  assert.equal(custom.notificationDetails({}).fields[0].label, 'User signal');
  assert.equal(custom.notificationDetails({}).fields[1].value, '{"ok":true}');
});

test('read IDs survive storage round trips and tolerate unavailable or malformed storage', () => {
  let value;
  globalThis.sessionStorage = { getItem: () => value, setItem: (_, next) => { value = next; } };
  saveNotificationIds(['one']);
  assert.deepEqual(readNotificationIds(), ['one']);
  value = '{broken';
  assert.deepEqual(readNotificationIds(), []);
  value = '{}';
  assert.deepEqual(readNotificationIds(), []);
  delete globalThis.sessionStorage;
  assert.deepEqual(readNotificationIds(), []);
  assert.doesNotThrow(() => saveNotificationIds(['two']));
});
