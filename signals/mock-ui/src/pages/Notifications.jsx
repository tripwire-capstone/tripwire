import { useState } from 'react';
import { Anchor, Box, Button, Checkbox, Collapse, Divider, Group, Stack, Text, UnstyledButton } from '@mantine/core';
import { Link } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import LaminarLink from '../components/LaminarLink';
import { useStoryData } from '../components/StoryClock';
import { notificationUrl, tripwireUrl } from '../components/links';
import { formatTime, plural } from '../components/format';
import classes from './Notifications.module.css';

function NotificationIcon({ severity, cluster = false }) {
  return (
    <Box component="span" c={cluster ? 'dimmed' : { critical: 'red', warning: 'orange', info: 'dimmed' }[severity]} className={classes.icon}>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {cluster ? <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5M3 16l9 5 9-5" /></> : severity === 'warning'
          ? <><path d="m12 3 10 18H2L12 3Z" /><path d="M12 9v5m0 3h.01" /></>
          : <><circle cx="12" cy="12" r="9" /><path d={severity === 'info' ? 'M12 11v6m0-10h.01' : 'M12 7v6m0 4h.01'} /></>}
      </svg>
    </Box>
  );
}

function NotificationRow({ notification: n, data }) {
  const [expanded, setExpanded] = useState(false);
  const d = data.notificationDetails(n);
  const markRead = () => data.markNotificationRead(n.id);
  const title = d.isCluster ? 'New cluster detected' : `${d.severity[0].toUpperCase()}${d.severity.slice(1)} event detected`;
  const detailsId = `notification-details-${n.id}`;
  return (
    <Box component="article" className={classes.row} data-unread={!n.read || undefined}>
      <NotificationIcon severity={d.severity} cluster={d.isCluster} />
      <Stack gap="sm" className={classes.copy}>
        <Group justify="space-between" align="flex-start" gap="xs">
          {d.tripwire ? <Anchor component={Link} to={notificationUrl(n)} onClick={markRead} c="inherit" fw={500} size="sm">{title}</Anchor> : <Text size="sm" fw={500}>{title}</Text>}
          <Group gap="xs"><Box component="span" className={classes.dot} hidden={n.read} aria-label="Unread" /><Text size="xs" c="dimmed">{formatTime(n.time)}</Text></Group>
        </Group>
        <Box component="dl" className={classes.metadata}>
          <Text component="dt" size="xs" c="dimmed">Signal</Text><Text component="dd" size="sm">{d.tripwire?.name || 'Signal unavailable'}</Text>
          <Text component="dt" size="xs" c="dimmed">Cluster</Text><Text component="dd" size="sm">{d.cluster?.name || 'Cluster unavailable'}</Text>
        </Box>
        {d.summary && <Text size="sm" c="dimmed">{d.summary}</Text>}
        <Collapse in={expanded}>
          <Stack gap="sm" id={detailsId} className={classes.details}>
            <Text size="sm" fw={500}>{d.isCluster ? 'Cluster details' : 'Extracted fields'}</Text>
            <Divider />
            {d.isCluster ? <>
              <Field label="Events" value={plural(d.eventCount, 'event')} />
              {d.firstSeen && <Field label="First seen" value={formatTime(d.firstSeen)} />}
              {d.lastSeen && <Field label="Last seen" value={formatTime(d.lastSeen)} />}
            </> : d.fields.length ? d.fields.map((field) => <Field key={field.label} {...field} />) : <Text size="sm" c="dimmed">No extracted fields available.</Text>}
          </Stack>
        </Collapse>
        <Group justify="space-between" gap="xs" className={classes.actions}>
          <Button variant="subtle" color="gray" size="xs" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded} aria-controls={detailsId}>{expanded ? 'Show less' : 'Show more'}</Button>
          <Group gap="md" className={classes.links}>
            {d.isCluster ? d.tripwire && d.cluster && <Anchor component={Link} to={tripwireUrl(n.tripwireId, { clusterId: n.clusterId })} onClick={markRead} c="dimmed" size="xs">View cluster ↗</Anchor> : <>
              {d.event?.traceId && <LaminarLink traceId={d.event.traceId} onClick={markRead}>View trace ↗</LaminarLink>}
              {d.tripwire && d.cluster && <Anchor component={Link} to={notificationUrl(n)} onClick={markRead} c="dimmed" size="xs">View similar events ↗</Anchor>}
            </>}
          </Group>
        </Group>
      </Stack>
    </Box>
  );
}

function Field({ label, value }) {
  return <Stack gap="xs"><Text size="xs" c="dimmed">{label}</Text><Text size="sm" className={classes.value}>{value}</Text></Stack>;
}

export default function Notifications() {
  const data = useStoryData();
  const [severity, setSeverity] = useState('all');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const counts = data.notificationCounts({ unreadOnly });
  const notifications = data.filteredNotifications({ severity, unreadOnly });
  return (
    <Stack gap="lg" maw={1100}>
      <PageHeader title="Notifications" description="View new clusters and signal event alerts." />
      <Box className={classes.filters} role="group" aria-label="Notification severity">
        {['all', 'critical', 'warning', 'info'].map((value) => <UnstyledButton key={value} className={classes.filter} aria-pressed={severity === value} onClick={() => setSeverity(value)}>
          {value !== 'all' && <NotificationIcon severity={value} />}<Text size="sm">{value[0].toUpperCase() + value.slice(1)}</Text><Text size="sm" c="dimmed">{counts[value]}</Text>
        </UnstyledButton>)}
      </Box>
      <Group justify="space-between" gap="sm">
        <Checkbox label="Unread only" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.currentTarget.checked)} />
        <Button variant="subtle" color="gray" size="xs" disabled={!data.unreadCount()} onClick={data.markAllNotificationsRead}>Mark all as read</Button>
      </Group>
      <Text size="xs" c="dimmed" aria-live="polite">{plural(notifications.length, 'notification')}</Text>
      <Box>{notifications.length ? notifications.map((n) => <NotificationRow key={n.id} notification={n} data={data} />) : <Text ta="center" c="dimmed" py="xl">{data.notifications.length ? 'No notifications match these filters.' : 'No notifications yet.'}</Text>}</Box>
    </Stack>
  );
}
