import { useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  Anchor, Badge, Box, Breadcrumbs, Button, Checkbox, CloseButton, Drawer, Group, Kbd, Menu, Pagination, Paper,
  SegmentedControl, Stack, Table, Text, TextInput, Title, Tooltip, UnstyledButton,
} from '@mantine/core';
import { useStoryData } from '../components/StoryClock';
import { SeverityBadge, KindBadge, StatusBadge } from '../components/Badges';
import { TimeBarChart } from '../components/Charts';
import TimeRangePicker from '../components/TimeRangePicker';
import PageHeader from '../components/PageHeader';
import TripwireForm from '../components/TripwireForm';
import { formatTime, formatRelative, formatCost, shortId, plural } from '../components/format';
import { tripwireListUrl, laminarTraceUrl } from '../components/links';
import { rangeFromParams, bucketMinutesFor, makeBuckets, bucketIndex, shortLabel } from '../components/timeRange';
import { clusterColor, cssColor, UNCLUSTERED_COLOR, RUN_COLORS } from '../theme';

// Tripwire detail, modeled on Laminar's Signal page: Events, Runs and Settings tabs with a time range.
// Address: /signals/<id>?tab=&cluster=&event=&pastHours=&pageNumber=&severity=&version=...

const PAGE_SIZE = 25;
const DEFAULT_HOURS = 72; // a signal's page opens on the past 3 days
const SEVERITIES = ['critical', 'warning', 'info'];

export default function TripwireDetail() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const data = useStoryData();
  const tripwire = data.getTripwire(id);

  if (!tripwire) {
    return (
      <PageHeader title="Signal not found" description={`There's no signal "${id}".`}
        actions={<Button component={Link} to={tripwireListUrl()}>All signals</Button>} />
    );
  }

  const tab = ['events', 'runs', 'settings'].includes(params.get('tab')) ? params.get('tab') : 'events';
  // Change some params, keep the rest. Changing anything but the page resets paging.
  const update = (changes) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) (v === null || v === undefined || v === '' ? next.delete(k) : next.set(k, v));
    if (!('pageNumber' in changes)) next.delete('pageNumber');
    setParams(next);
  };

  return (
    <Stack gap="md">
      <Group gap="xs" align="baseline">
        <Breadcrumbs separator="/">
          <Anchor component={Link} to={tripwireListUrl()} c="dimmed" size="lg">Signals</Anchor>
          <Title order={3}>{tripwire.name}</Title>
        </Breadcrumbs>
        <KindBadge kind={tripwire.kind} />
        <StatusBadge status={tripwire.status} />
      </Group>

      <Group gap="sm">
        <SegmentedControl value={tab} onChange={(v) => update({ tab: v === 'events' ? null : v, event: null, section: null })}
          data={[{ value: 'events', label: 'Events' }, { value: 'runs', label: 'Runs' }, { value: 'settings', label: 'Settings' }]} />
        {tab !== 'settings' && <TimeRangePicker defaultHours={DEFAULT_HOURS} />}
      </Group>

      {tab === 'events' && <EventsTab tripwire={tripwire} params={params} update={update} />}
      {tab === 'runs' && <RunsTab tripwire={tripwire} params={params} update={update} />}
      {tab === 'settings' && <SettingsTab tripwire={tripwire} params={params} update={update} />}

      <EventDrawer tripwire={tripwire} eventId={params.get('event')} update={update} />
    </Stack>
  );
}

// ---------------------------------------------------------------- Events tab

function EventsTab({ tripwire, params, update }) {
  const data = useStoryData();
  const range = rangeFromParams(params, data.asOf, DEFAULT_HOURS);
  const inRange = data.eventsBetween(tripwire.id, range.start, range.end);

  // Selected cluster (ignored if it doesn't exist or belongs to another tripwire).
  const selectedParam = params.get('cluster');
  const selected = selectedParam && data.getCluster(selectedParam)?.tripwireId === tripwire.id ? data.getCluster(selectedParam) : null;
  const top = selected ? data.getCluster(selected.parentId ?? selected.id) : null;

  // The strip shows top-level clusters, or the sub-clusters of the selected one. Sized by events in range.
  const countIn = (clusterId) => { const ids = data.leafIdsOf(clusterId); return inRange.filter((e) => ids.includes(e.clusterId)).length; };
  const stripClusters = (top ? data.subClusters(top.id) : data.topClusters(tripwire.id))
    .map((c) => ({ ...c, count: countIn(c.id) })).filter((c) => c.count > 0).sort((a, b) => b.count - a.count)
    .map((c, i) => ({ ...c, color: clusterColor(i) }));
  const topCount = data.topClusters(tripwire.id).filter((c) => countIn(c.id) > 0).length;

  // Events shown: in range, in the selected cluster, matching filters and search.
  const severity = params.get('severity');
  const version = params.get('version');
  const [search, setSearch] = useState('');
  const shown = inRange.filter((e) => {
    if (selected && !data.leafIdsOf(selected.id).includes(e.clusterId)) return false;
    if (severity && e.severity !== severity) return false;
    if (version && e.appVersion !== version) return false;
    if (search) {
      const hay = [e.severity, e.traceId, e.id, ...Object.values(e.payload)].join(' ').toLowerCase();
      if (!hay.includes(search.toLowerCase())) return false;
    }
    return true;
  });

  // Chart: one stacked series per strip cluster (+ "Not in a cluster" at the top level).
  const minutes = bucketMinutesFor(range.hours);
  const buckets = makeBuckets(range.start, range.end, minutes);
  const seriesOf = (e) => {
    if (!e.clusterId) return 'unclustered';
    const c = stripClusters.find((s) => data.leafIdsOf(s.id).includes(e.clusterId));
    return c ? c.id : null;
  };
  const series = [
    ...stripClusters.map((c) => ({ name: c.id, label: c.name, color: c.color })),
    ...(top ? [] : [{ name: 'unclustered', label: 'Not in a cluster', color: UNCLUSTERED_COLOR }]),
  ];
  const chartEvents = inRange.filter((e) => (selected ? data.leafIdsOf(selected.id).includes(e.clusterId) : true));
  const rows = buckets.map((b) => Object.fromEntries([['bucket', b.start], ...series.map((s) => [s.name, 0])]));
  for (const e of chartEvents) {
    const i = bucketIndex(buckets, e.time); const s = seriesOf(e);
    if (i >= 0 && s && rows[i][s] !== undefined) rows[i][s] += 1;
  }
  const markers = versionMarkers(data.versions, buckets);

  // Table columns: time, severity, the tripwire's own payload fields, trace, id.
  const allColumns = [
    { key: 'time', label: 'Timestamp' }, { key: 'severity', label: 'Severity', w: 110 },
    ...tripwire.payloadFields.map((f) => ({ key: `payload.${f.name}`, label: f.name, payload: f.name })),
    { key: 'trace', label: 'Trace ID' }, { key: 'id', label: 'ID' },
  ];
  const [hidden, setHidden] = useState([]);
  const columns = allColumns.filter((c) => !hidden.includes(c.key));
  const page = Math.max(0, Number(params.get('pageNumber')) || 0);
  const pageRows = shown.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const openEventId = params.get('event');

  const filterGroups = [
    { key: 'severity', label: 'Severity', options: SEVERITIES },
    { key: 'version', label: 'App version', options: data.versions.map((v) => v.version) },
  ];

  return (
    <Stack gap="sm">
      <ClusterStrip clusters={stripClusters} selectedId={selected?.id} onSelect={(c) => update({ cluster: c.id, event: null })} />
      <Group gap={6}>
        <Text size="sm" c="dimmed">Event clusters</Text>
        <Text size="sm" c="dimmed">·</Text>
        <UnstyledButton onClick={() => update({ cluster: null })}>
          <Text size="sm" fw={600}>{plural(topCount, 'cluster')}</Text>
        </UnstyledButton>
        {top && <><Text size="sm" c="dimmed">›</Text>
          <UnstyledButton onClick={() => update({ cluster: top.id })}><Text size="sm" fw={selected?.id === top.id ? 600 : 400}>{top.name}</Text></UnstyledButton></>}
        {selected && selected.parentId && <><Text size="sm" c="dimmed">›</Text><Text size="sm" fw={600}>{selected.name}</Text></>}
        {selected && <CloseButton size="sm" aria-label="Show all clusters" onClick={() => update({ cluster: null })} />}
      </Group>

      <Paper withBorder p="md">
        <TimeBarChart data={rows} series={series} minutes={minutes} markers={markers} h={190} />
      </Paper>

      <Toolbar filterGroups={filterGroups} params={params} update={update} columns={allColumns} hidden={hidden} setHidden={setHidden} />
      <SearchBox value={search} onChange={setSearch} placeholder="Search events by payload, severity, trace id, and more…" />

      <Table.ScrollContainer minWidth={900}>
        <Table highlightOnHover verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>{columns.map((c) => <Table.Th key={c.key} w={c.w}>{c.label}</Table.Th>)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pageRows.length === 0 && (
              <Table.Tr><Table.Td colSpan={columns.length}>
                <Text ta="center" c="dimmed" py="md">
                  {inRange.length === 0 ? `No events in the last ${range.absolute ? 'selected range' : shortLabel(range.hours)}` : 'No events match these filters'}
                </Text>
              </Table.Td></Table.Tr>
            )}
            {pageRows.map((e) => (
              <Table.Tr key={e.id} onClick={() => update({ event: e.id, pageNumber: String(page) })} style={{ cursor: 'pointer' }}
                bg={e.id === openEventId ? 'var(--mantine-color-dark-5)' : undefined}>
                {columns.map((c) => <Table.Td key={c.key} style={c.payload ? { maxWidth: 460 } : undefined}>{eventCell(c, e, data.asOf)}</Table.Td>)}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Paging total={shown.length} page={page} update={update} />
    </Stack>
  );
}

function eventCell(column, e, now) {
  switch (column.key) {
    case 'time': return <Tooltip label={formatTime(e.time)}><Text size="sm" style={{ whiteSpace: 'nowrap' }}>{formatRelative(e.time, now)}</Text></Tooltip>;
    case 'severity': return <SeverityBadge severity={e.severity} />;
    case 'trace': return (
      <Anchor href={laminarTraceUrl(e.traceId)} target="_blank" rel="noreferrer" size="sm" ff="monospace"
        onClick={(ev) => ev.stopPropagation()} style={{ whiteSpace: 'nowrap' }}>{shortId(e.traceId, 13)} ↗</Anchor>
    );
    case 'id': return <Text size="sm" ff="monospace" c="dimmed">{shortId(e.id)}</Text>;
    default: return <Text size="sm" lineClamp={3}>{String(e.payload[column.payload] ?? '—')}</Text>;
  }
}

// Colored bars, one per cluster, sized by event count (Laminar's cluster strip).
function ClusterStrip({ clusters, selectedId, onSelect }) {
  if (clusters.length === 0) {
    return <Text size="sm" c="dimmed">No clusters in this time range.</Text>;
  }
  return (
    <Group gap={6} wrap="nowrap" style={{ overflow: 'hidden' }}>
      {clusters.map((c) => (
        <Tooltip key={c.id} label={`${c.name} · ${plural(c.count, 'event')}`} withArrow>
          <UnstyledButton onClick={() => onSelect(c)}
            style={{
              flex: `${c.count} 1 0`, minWidth: 48, padding: '3px 10px', borderRadius: 'var(--mantine-radius-xl)',
              background: cssColor(c.color), opacity: selectedId && clusters.some((x) => x.id === selectedId) && selectedId !== c.id ? 0.45 : 1,
              outline: selectedId === c.id ? '2px solid var(--mantine-color-gray-3)' : 'none',
            }}>
            <Text size="sm" c="white" truncate="end">{c.name}</Text>
          </UnstyledButton>
        </Tooltip>
      ))}
    </Group>
  );
}

// Version markers: where the app version changed (the first version ever seen isn't a change).
function versionMarkers(versions, buckets) {
  return versions
    .slice(1)
    .map((v) => ({ v, i: bucketIndex(buckets, v.firstSeenAt) }))
    .filter(({ i }) => i >= 0)
    .map(({ v, i }) => ({ at: buckets[i].start, label: v.version }));
}

// ---------------------------------------------------------------- Runs tab

function RunsTab({ tripwire, params, update }) {
  const data = useStoryData();
  const range = rangeFromParams(params, data.asOf, DEFAULT_HOURS);
  const inRange = data.runsBetween(tripwire.id, range.start, range.end);
  const [search, setSearch] = useState('');
  const status = params.get('status');
  const source = params.get('source');
  const outcome = params.get('outcome');
  const outcomeOf = (r) => (r.status === 'failed' ? 'failed' : r.eventId ? 'event' : 'noEvent');

  const shown = inRange.filter((r) => {
    if (status && r.status !== status) return false;
    if (source && r.source !== source) return false;
    if (outcome && (outcome === 'event') !== Boolean(r.eventId)) return false;
    if (search) {
      const hay = [r.status, r.source, r.id, r.traceId, r.eventId ?? ''].join(' ').toLowerCase();
      if (!hay.includes(search.toLowerCase())) return false;
    }
    return true;
  });

  const minutes = bucketMinutesFor(range.hours);
  const buckets = makeBuckets(range.start, range.end, minutes);
  const series = [
    { name: 'event', label: 'Event', color: RUN_COLORS.event },
    { name: 'noEvent', label: 'No event', color: RUN_COLORS.noEvent },
    { name: 'failed', label: 'Failed', color: RUN_COLORS.failed },
  ];
  const rows = buckets.map((b) => ({ bucket: b.start, event: 0, noEvent: 0, failed: 0 }));
  for (const r of shown) { const i = bucketIndex(buckets, r.time); if (i >= 0) rows[i][outcomeOf(r)] += 1; }

  const allColumns = [
    { key: 'status', label: 'Status' }, { key: 'source', label: 'Source' }, { key: 'event', label: 'Event' },
    { key: 'clusters', label: 'Clusters' }, { key: 'cost', label: 'Cost' }, { key: 'time', label: 'Updated at' },
    { key: 'eventId', label: 'Event ID' },
  ];
  const [hidden, setHidden] = useState([]);
  const columns = allColumns.filter((c) => !hidden.includes(c.key));
  const page = Math.max(0, Number(params.get('pageNumber')) || 0);
  const pageRows = shown.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const filterGroups = [
    { key: 'status', label: 'Status', options: ['completed', 'failed'] },
    { key: 'source', label: 'Source', options: ['trigger', 'backfill'] },
    { key: 'outcome', label: 'Event', options: ['event', 'no event'], values: ['event', 'none'] },
  ];

  return (
    <Stack gap="sm">
      <Toolbar filterGroups={filterGroups} params={params} update={update} columns={allColumns} hidden={hidden} setHidden={setHidden} />
      <SearchBox value={search} onChange={setSearch} placeholder="Filter by status, run id, trace id…" />
      <Paper withBorder p="md">
        <TimeBarChart data={rows} series={series} minutes={minutes} markers={versionMarkers(data.versions, buckets)} h={170} />
        <Text size="sm" c="dimmed" mt="xs">Total: {shown.length}</Text>
      </Paper>

      <Table.ScrollContainer minWidth={900}>
        <Table highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>{columns.map((c) => <Table.Th key={c.key}>{c.label}</Table.Th>)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pageRows.length === 0 && (
              <Table.Tr><Table.Td colSpan={columns.length}>
                <Text ta="center" c="dimmed" py="md">{inRange.length === 0 ? 'No runs in this time range' : 'No runs match these filters'}</Text>
              </Table.Td></Table.Tr>
            )}
            {pageRows.map((r) => <Table.Tr key={r.id}>{columns.map((c) => <Table.Td key={c.key}>{runCell(c, r, data, update)}</Table.Td>)}</Table.Tr>)}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Paging total={shown.length} page={page} update={update} />
    </Stack>
  );
}

function runCell(column, r, data, update) {
  switch (column.key) {
    case 'status': return r.status === 'failed'
      ? <Text size="sm" c="red.5">✕ Failed</Text>
      : <Text size="sm" c="dimmed">✓ Completed</Text>;
    case 'source': return <Text size="sm">{r.source === 'backfill' ? 'Backfill' : 'Trigger'}</Text>;
    case 'event': return r.eventId
      ? <Anchor size="sm" onClick={() => update({ tab: null, event: r.eventId, cluster: null })}>Event ↗</Anchor>
      : <Anchor size="sm" c="dimmed" href={laminarTraceUrl(r.traceId)} target="_blank" rel="noreferrer">No event ↗</Anchor>;
    case 'clusters': {
      const c = r.clusterId && data.getCluster(r.clusterId);
      return c ? <Text size="sm">{c.name}</Text> : <Text size="sm" c="dimmed">-</Text>;
    }
    case 'cost': return <Text size="sm" ff="monospace">{formatCost(r.cost)}</Text>;
    case 'time': return <Text size="sm" style={{ whiteSpace: 'nowrap' }}>{formatTime(r.time)}</Text>;
    case 'eventId': return <Text size="sm" ff="monospace" c="dimmed">{r.eventId ? shortId(r.eventId) : '-'}</Text>;
    default: return null;
  }
}

// ---------------------------------------------------------------- Settings tab
// Laminar's section menu (General / Versions / Alerts / Backfill). General is the shared TripwireForm in edit mode;
// the others are placeholders until we've seen Laminar's versions.

const SECTIONS = [
  { id: 'general', label: 'General' },
  { id: 'versions', label: 'Versions' },
  { id: 'alerts', label: 'Alerts' },
  { id: 'backfill', label: 'Backfill' },
];

function SettingsTab({ tripwire, params, update }) {
  const section = SECTIONS.find((x) => x.id === params.get('section')) ?? SECTIONS[0];
  return (
    <Group align="flex-start" gap="xl" wrap="nowrap">
      <Stack gap={2} w={180} style={{ flexShrink: 0 }}>
        {SECTIONS.map((x) => (
          <UnstyledButton key={x.id} onClick={() => update({ section: x.id === 'general' ? null : x.id })} px="sm" py={6}
            style={{ borderRadius: 'var(--mantine-radius-md)', background: x.id === section.id ? 'var(--mantine-color-dark-5)' : undefined }}>
            <Text size="sm" fw={x.id === section.id ? 600 : 400}>{x.label}</Text>
          </UnstyledButton>
        ))}
      </Stack>
      <Stack gap="xs" style={{ flex: 1, minWidth: 0 }}>
        <Title order={3}>{section.label}</Title>
        {section.id === 'general'
          ? <TripwireForm mode="edit" initialValues={tripwire} />
          : <Text c="dimmed">Placeholder</Text>}
      </Stack>
    </Group>
  );
}

// ---------------------------------------------------------------- shared bits of this page

// Add filter · Columns · Refresh, plus the active filters as removable chips.
function Toolbar({ filterGroups, params, update, columns, hidden, setHidden }) {
  const active = filterGroups.filter((g) => params.get(g.key));
  return (
    <Group gap="xs">
      <Menu position="bottom-start" withinPortal>
        <Menu.Target><Button variant="default" size="xs">+ Add filter</Button></Menu.Target>
        <Menu.Dropdown>
          {filterGroups.map((g) => (
            <div key={g.key}>
              <Menu.Label>{g.label}</Menu.Label>
              {g.options.map((o, i) => (
                <Menu.Item key={o} onClick={() => update({ [g.key]: g.values ? g.values[i] : o })}>{o}</Menu.Item>
              ))}
            </div>
          ))}
        </Menu.Dropdown>
      </Menu>
      <Menu position="bottom-start" closeOnItemClick={false} withinPortal>
        <Menu.Target><Button variant="default" size="xs">Columns</Button></Menu.Target>
        <Menu.Dropdown>
          {columns.map((c) => (
            <Menu.Item key={c.key}>
              <Checkbox size="xs" label={c.label} checked={!hidden.includes(c.key)}
                onChange={() => setHidden(hidden.includes(c.key) ? hidden.filter((k) => k !== c.key) : [...hidden, c.key])} />
            </Menu.Item>
          ))}
        </Menu.Dropdown>
      </Menu>
      <Button variant="default" size="xs">↻ Refresh</Button>
      {active.map((g) => {
        const value = params.get(g.key);
        const label = g.values ? g.options[g.values.indexOf(value)] : value;
        return (
          <Badge key={g.key} variant="light" color="gray" size="lg" tt="none"
            rightSection={<CloseButton size="xs" aria-label={`Remove ${g.label} filter`} onClick={() => update({ [g.key]: null })} />}>
            {g.label}: {label}
          </Badge>
        );
      })}
    </Group>
  );
}

function SearchBox({ value, onChange, placeholder }) {
  return (
    <TextInput id="detail-search" value={value} onChange={(e) => onChange(e.currentTarget.value)} placeholder={placeholder}
      leftSection={<SearchIcon />} rightSection={<Kbd size="xs">⌘K</Kbd>} rightSectionWidth={44} />
  );
}

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
    </svg>
  );
}

function Paging({ total, page, update }) {
  const pages = Math.ceil(total / PAGE_SIZE);
  if (pages <= 1) return null;
  return (
    <Group justify="space-between">
      <Text size="sm" c="dimmed">{page * PAGE_SIZE + 1}–{Math.min(total, (page + 1) * PAGE_SIZE)} of {total}</Text>
      <Pagination size="sm" total={pages} value={page + 1} onChange={(p) => update({ pageNumber: String(p - 1) })} />
    </Group>
  );
}

// Side panel for one event: payload, conversation, ids, link to the trace in Laminar.
function EventDrawer({ tripwire, eventId, update }) {
  const data = useStoryData();
  const event = eventId ? data.getEvent(eventId) : null;
  const valid = event && event.tripwireId === tripwire.id;
  const cluster = valid && event.clusterId ? data.getCluster(event.clusterId) : null;
  return (
    <Drawer opened={Boolean(valid)} onClose={() => update({ event: null, pageNumber: undefined })} position="right" size="lg"
      title={<Group gap="xs"><Text fw={600}>Event</Text>{valid && <SeverityBadge severity={event.severity} />}</Group>}>
      {valid && (
        <Stack gap="md">
          <Text size="sm" c="dimmed">{formatTime(event.time)} · {formatRelative(event.time, data.asOf)}</Text>

          <Box>
            <Label>Cluster</Label>
            {cluster ? (
              <Group gap="xs">
                <Text size="sm">{cluster.name}</Text>
                <Anchor size="sm" onClick={() => update({ cluster: cluster.id, event: null, tab: null })}>Show cluster</Anchor>
              </Group>
            ) : <Text size="sm" c="dimmed">Not in a cluster yet</Text>}
          </Box>

          {tripwire.payloadFields.map((f) => (
            <Box key={f.name}>
              <Label>{f.name}</Label>
              <Text size="sm">{String(event.payload[f.name] ?? '—')}</Text>
            </Box>
          ))}

          <Box>
            <Label>Conversation turn</Label>
            {event.excerpt.visitor ? (
              <Stack gap="xs">
                <Paper withBorder p="sm"><Text size="xs" c="dimmed" mb={2}>Visitor</Text><Text size="sm">{event.excerpt.visitor}</Text></Paper>
                <Paper withBorder p="sm"><Text size="xs" c="dimmed" mb={2}>Assistant</Text><Text size="sm">{event.excerpt.reply}</Text></Paper>
              </Stack>
            ) : <Text size="sm" c="dimmed">Open the trace in Laminar to see the conversation.</Text>}
          </Box>

          <Table verticalSpacing={4}>
            <Table.Tbody>
              {[['Event ID', event.id], ['Run ID', event.runId], ['Trace ID', event.traceId], ['Conversation', event.conversationId], ['App version', event.appVersion]].map(([k, v]) => (
                <Table.Tr key={k}><Table.Td><Text size="xs" c="dimmed">{k}</Text></Table.Td><Table.Td><Text size="xs" ff="monospace">{v}</Text></Table.Td></Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          <Group><Button component="a" href={laminarTraceUrl(event.traceId)} target="_blank" rel="noreferrer">Open trace in Laminar ↗</Button></Group>
        </Stack>
      )}
    </Drawer>
  );
}

function Label({ children }) {
  return <Text size="xs" c="dimmed" tt="uppercase" fw={600} mb={2} style={{ letterSpacing: '0.04em' }}>{children}</Text>;
}
