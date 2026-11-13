import { useState } from 'react';
import { useDebouncedValue } from '@mantine/hooks';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  Alert, Anchor, Badge, Box, Breadcrumbs, Button, Checkbox, CloseButton, Drawer, Group, Kbd, Menu, Pagination, Paper,
  SegmentedControl, Stack, Table, Text, TextInput, Title, Tooltip, UnstyledButton,
} from '@mantine/core';
import {
  FEATURES, editSignal, setActive, testRun, useClusters, useEventCounts, useEvents, useRunCounts, useRuns, useSignal,
} from '../api';
import { SeverityBadge, KindBadge, StatusBadge } from '../components/Badges';
import { TimeBarChart } from '../components/Charts';
import TimeRangePicker from '../components/TimeRangePicker';
import PageHeader from '../components/PageHeader';
import TripwireForm from '../components/TripwireForm';
import { formatTime, formatRelative, formatCost, shortId, plural } from '../components/format';
import { tripwireListUrl } from '../components/links';
import { rangeFromParams, bucketMinutesFor, makeBuckets, bucketIndex, shortLabel } from '../components/timeRange';
import { clusterColor, cssColor, UNCLUSTERED_COLOR, RUN_COLORS, CLUSTER_COLORS } from '../theme';

// A signal's page, modeled on Laminar's Signal page: Events, Runs and Settings tabs with a time range.
// Address: /signals/<id>?tab=&event=&pastHours=&pageNumber=&severity=...
// Reads the real API. Counts, charts, filters, search and paging all happen in the database, so every
// number covers the whole time range, not just the rows loaded. App versions stay hidden (src/api/features.js).

const PAGE_SIZE = 25;
const DEFAULT_HOURS = 72; // a signal's page opens on the past 3 days
const SEVERITIES = ['critical', 'warning', 'info'];
const SEARCH_PAUSE_MS = 300; // search runs after a short pause in typing, not on every keystroke
const OTHER_CLUSTERS_COLOR = 'gray.6';

// "Now" in the pages' time format (UTC, no zone). Read once, and again when Refresh is clicked.
const currentTime = () => new Date().toISOString().slice(0, 19);

export default function TripwireDetail() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const { data: tripwire, error, loading, reload } = useSignal(id);
  const [now, setNow] = useState(currentTime);
  const [notice, setNotice] = useState('');
  const [toggling, setToggling] = useState(false);

  if (!tripwire && loading) return <Text c="dimmed">Loading…</Text>;
  if (!tripwire && (error?.status === 404 || error?.status === 422)) {
    return (
      <PageHeader title="Signal not found" description={`There's no signal "${id}".`}
        actions={<Button component={Link} to={tripwireListUrl()}>All signals</Button>} />
    );
  }
  if (!tripwire) {
    return (
      <Alert color="red" title="Couldn't load this signal">
        <Group justify="space-between"><Text size="sm">{error?.message}</Text>
          <Button variant="light" color="gray" onClick={reload}>Try again</Button></Group>
      </Alert>
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
  const refresh = () => setNow(currentTime());

  async function toggleActive() {
    setToggling(true);
    try {
      const saved = await setActive(tripwire.id, tripwire.status !== 'active');
      setNotice(saved.status === 'active' ? 'Signal turned on: it judges new traces again.' : 'Signal turned off: new traces get no runs.');
      reload();
    } catch (e) {
      setNotice(`Couldn't change it: ${e.message}`);
    } finally {
      setToggling(false);
    }
  }

  function saved(signal) {
    setNotice(`Saved. ${signal.name} is on version ${signal.versionNumber}.`);
    reload();
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group gap="xs" align="baseline">
          <Breadcrumbs separator="/">
            <Anchor component={Link} to={tripwireListUrl()} c="dimmed" size="lg">Signals</Anchor>
            <Title order={3}>{tripwire.name}</Title>
          </Breadcrumbs>
          <KindBadge kind={tripwire.kind} />
          <StatusBadge status={tripwire.status} />
        </Group>
        <Button variant="light" color="gray" size="xs" loading={toggling} onClick={toggleActive}>
          {tripwire.status === 'active' ? 'Turn off' : 'Turn on'}
        </Button>
      </Group>
      {notice && <Text role="status" size="sm" c="dimmed">{notice}</Text>}

      <Group gap="sm">
        <SegmentedControl value={tab} onChange={(v) => update({ tab: v === 'events' ? null : v, event: null, section: null })}
          data={[{ value: 'events', label: 'Events' }, { value: 'runs', label: 'Runs' }, { value: 'settings', label: 'Settings' }]} />
        {tab !== 'settings' && <TimeRangePicker defaultHours={DEFAULT_HOURS} now={now} />}
      </Group>

      {tab === 'events' && <EventsTab tripwire={tripwire} params={params} update={update} now={now} onRefresh={refresh} />}
      {tab === 'runs' && <RunsTab tripwire={tripwire} params={params} update={update} now={now} onRefresh={refresh} />}
      {tab === 'settings' && <SettingsTab tripwire={tripwire} params={params} update={update} onSaved={saved} />}
    </Stack>
  );
}

function LoadError({ error }) {
  return error ? <Alert color="red" title="Couldn't load">{error.message}</Alert> : null;
}

// ---------------------------------------------------------------- Events tab

// Colors by rank: the top clusters (most events in the range) get their own color, the rest share
// "Other clusters", events in no cluster are gray. The strip and the chart use the same function.
function clusterColors(clusters) {
  const top = new Map(clusters.slice(0, CLUSTER_COLORS.length).map((c, i) => [c.id, clusterColor(i)]));
  return (clusterId) => (clusterId == null ? UNCLUSTERED_COLOR : top.get(clusterId) ?? OTHER_CLUSTERS_COLOR);
}

function EventsTab({ tripwire, params, update, now, onRefresh }) {
  const range = rangeFromParams(params, now, DEFAULT_HOURS);
  const severity = params.get('severity');
  const clusterId = params.get('cluster') ? Number(params.get('cluster')) : null;
  const [search, setSearch] = useState('');
  const [q] = useDebouncedValue(search.trim(), SEARCH_PAUSE_MS);
  const page = Math.max(0, Number(params.get('pageNumber')) || 0);

  const clustersQuery = useClusters(tripwire.id, range);
  const clusterData = clustersQuery.data ?? { events: 0, clusteredEvents: 0, clusters: [] };
  const colorOf = clusterColors(clusterData.clusters);

  // Table: one page from the database, with every filter applied there.
  const { data, error, loading } = useEvents(tripwire.id, range, {
    severity, cluster: clusterId, q, offset: page * PAGE_SIZE, limit: PAGE_SIZE,
  });
  const total = data?.total ?? 0;
  const pageRows = data?.events ?? [];

  // Chart: events per bucket and cluster, same severity and search filters. A selected cluster shows alone.
  const minutes = bucketMinutesFor(range.hours);
  const buckets = makeBuckets(range.start, range.end, minutes);
  const counts = useEventCounts(tripwire.id, range, minutes, { severity, q });
  const keyOf = (id) => {
    if (id == null) return 'none';
    return clusterData.clusters.slice(0, CLUSTER_COLORS.length).some((c) => c.id === id) ? `c${id}` : 'other';
  };
  const named = clusterData.clusters.slice(0, CLUSTER_COLORS.length)
    .map((c) => ({ name: `c${c.id}`, label: c.name, color: colorOf(c.id) }));
  const series = clusterId != null
    ? [{ name: 'selected', label: clusterData.clusters.find((c) => c.id === clusterId)?.name ?? 'Cluster', color: colorOf(clusterId) }]
    : [...named, { name: 'other', label: 'Other clusters', color: OTHER_CLUSTERS_COLOR },
      { name: 'none', label: 'Not in a cluster', color: UNCLUSTERED_COLOR }];
  const rows = buckets.map((b) => Object.fromEntries([['bucket', b.start], ...series.map((x) => [x.name, 0])]));
  for (const c of counts.data ?? []) {
    const i = bucketIndex(buckets, c.bucket);
    if (i < 0) continue;
    if (clusterId != null) { if (c.clusterId === clusterId) rows[i].selected += c.count; } else rows[i][keyOf(c.clusterId)] += c.count;
  }

  // Table columns: time, severity, the signal's own fields, trace, id.
  const allColumns = [
    { key: 'time', label: 'Timestamp' }, { key: 'severity', label: 'Severity', w: 110 },
    ...tripwire.payloadFields.map((f) => ({ key: `payload.${f.name}`, label: f.name, payload: f.name })),
    { key: 'trace', label: 'Trace ID' }, { key: 'id', label: 'ID' },
  ];
  const [hidden, setHidden] = useState([]);
  const columns = allColumns.filter((c) => !hidden.includes(c.key));
  const openEventId = params.get('event');
  const openEvent = pageRows.find((e) => String(e.id) === openEventId) ?? null;

  const filterGroups = [
    { key: 'severity', label: 'Severity', options: SEVERITIES },
    // Shown whenever a cluster is selected, even one with no events in this range, so a filter is never invisible.
    ...(clusterData.clusters.length || clusterId != null ? [{
      key: 'cluster', label: 'Cluster',
      options: clusterData.clusters.map((c) => c.name), values: clusterData.clusters.map((c) => String(c.id)),
    }] : []),
  ];
  const filtered = Boolean(severity || clusterId != null || q);

  return (
    <Stack gap="sm">
      <Stack gap={4}>
        <ClusterStrip clusters={clusterData.clusters} colorOf={colorOf} selectedId={clusterId}
          onSelect={(c) => update({ cluster: c.id === clusterId ? null : String(c.id), event: null })} />
        {clusterData.events > 0 && (
          <Text size="xs" c="dimmed">
            {clusterData.clusteredEvents.toLocaleString()} of {plural(clusterData.events, 'event')} in this range {clusterData.clusteredEvents === 1 ? 'is' : 'are'} in a cluster
          </Text>
        )}
      </Stack>
      <LoadError error={error ?? counts.error ?? clustersQuery.error} />

      <Paper withBorder p="md">
        <TimeBarChart data={rows} series={series} minutes={minutes} markers={[]} h={190} />
      </Paper>

      <Toolbar filterGroups={filterGroups} params={params} update={update} columns={allColumns} hidden={hidden}
        setHidden={setHidden} onRefresh={onRefresh} />
      <SearchBox value={search} onChange={(v) => { setSearch(v); if (page) update({ pageNumber: null }); }}
        placeholder="Search events by field values, severity, trace id, evidence…" />

      <Table.ScrollContainer minWidth={900}>
        <Table highlightOnHover verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>{columns.map((c) => <Table.Th key={c.key} w={c.w}>{c.label}</Table.Th>)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pageRows.length === 0 && (
              <Table.Tr><Table.Td colSpan={columns.length}>
                <Text ta="center" c="dimmed" py="md">
                  {loading && !data ? 'Loading…'
                    : !filtered ? `No events in the last ${range.absolute ? 'selected range' : shortLabel(range.hours)}`
                      : 'No events match these filters'}
                </Text>
              </Table.Td></Table.Tr>
            )}
            {pageRows.map((e) => (
              <Table.Tr key={e.id} onClick={() => update({ event: String(e.id), pageNumber: String(page) })} style={{ cursor: 'pointer' }}
                bg={String(e.id) === openEventId ? 'var(--mantine-color-dark-5)' : undefined}>
                {columns.map((c) => <Table.Td key={c.key} style={c.payload ? { maxWidth: 460 } : undefined}>{eventCell(c, e, now)}</Table.Td>)}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Paging total={total} page={page} update={update} />

      <EventDrawer tripwire={tripwire} event={openEvent} now={now} update={update} />
    </Stack>
  );
}

function TraceLink({ traceId, url }) {
  return url ? (
    <Anchor href={url} target="_blank" rel="noreferrer" size="sm" ff="monospace"
      onClick={(ev) => ev.stopPropagation()} style={{ whiteSpace: 'nowrap' }}>{shortId(traceId, 13)} ↗</Anchor>
  ) : <Text size="sm" ff="monospace">{shortId(traceId, 13)}</Text>;
}

function eventCell(column, e, now) {
  switch (column.key) {
    case 'time': return <Tooltip label={formatTime(e.time)}><Text size="sm" style={{ whiteSpace: 'nowrap' }}>{formatRelative(e.time, now)}</Text></Tooltip>;
    case 'severity': return <SeverityBadge severity={e.severity} />;
    case 'trace': return <TraceLink traceId={e.traceId} url={e.laminarUrl} />;
    case 'id': return <Text size="sm" ff="monospace" c="dimmed">{e.id}</Text>;
    default: return <Text size="sm" lineClamp={3}>{String(e.payload[column.payload] ?? '—')}</Text>;
  }
}

// Colored bars, one per cluster with events in the range, sized by its share of the clustered events
// (Laminar's cluster strip). Events in no cluster aren't in the strip; the caption below it counts them.
// Clicking a cluster filters the chart and table to it; clicking it again clears the filter.
function ClusterStrip({ clusters, colorOf, selectedId, onSelect }) {
  if (clusters.length === 0) {
    return <Text size="sm" c="dimmed">No clusters in this time range.</Text>;
  }
  return (
    <Group gap={6} wrap="nowrap" style={{ overflow: 'hidden' }}>
      {clusters.map((c) => (
        <Tooltip key={c.id} label={`${c.name} · ${plural(c.count, 'event')}`} withArrow>
          <UnstyledButton onClick={() => onSelect(c)} aria-pressed={selectedId === c.id}
            style={{
              flex: `${c.count} 1 0`, minWidth: 48, padding: '3px 10px', borderRadius: 'var(--mantine-radius-xl)',
              background: cssColor(colorOf(c.id)), opacity: selectedId != null && selectedId !== c.id ? 0.45 : 1,
              outline: selectedId === c.id ? '2px solid var(--mantine-color-gray-3)' : 'none',
            }}>
            <Text size="sm" c="white" truncate="end">{c.name}</Text>
          </UnstyledButton>
        </Tooltip>
      ))}
    </Group>
  );
}

// ---------------------------------------------------------------- Runs tab

const RUN_STATUSES = ['completed', 'failed', 'pending', 'running'];

function RunsTab({ tripwire, params, update, now, onRefresh }) {
  const range = rangeFromParams(params, now, DEFAULT_HOURS);
  const [search, setSearch] = useState('');
  const [q] = useDebouncedValue(search.trim(), SEARCH_PAUSE_MS);
  const filters = { status: params.get('status'), source: params.get('source'), outcome: params.get('outcome'), q };
  const page = Math.max(0, Number(params.get('pageNumber')) || 0);
  const { data, error, loading } = useRuns(tripwire.id, range, { ...filters, offset: page * PAGE_SIZE, limit: PAGE_SIZE });
  const total = data?.total ?? 0;
  const pageRows = data?.runs ?? [];

  // Chart: finished runs per bucket by outcome, same filters (pending and running runs aren't in it).
  const minutes = bucketMinutesFor(range.hours);
  const buckets = makeBuckets(range.start, range.end, minutes);
  const counts = useRunCounts(tripwire.id, range, minutes, filters);
  const series = [
    { name: 'event', label: 'Event', color: RUN_COLORS.event },
    { name: 'noEvent', label: 'No event', color: RUN_COLORS.noEvent },
    { name: 'failed', label: 'Failed', color: RUN_COLORS.failed },
  ];
  const rows = buckets.map((b) => ({ bucket: b.start, event: 0, noEvent: 0, failed: 0 }));
  for (const c of counts.data ?? []) {
    const i = bucketIndex(buckets, c.bucket);
    if (i >= 0) for (const k of ['event', 'noEvent', 'failed']) rows[i][k] += c[k];
  }

  const allColumns = [
    { key: 'status', label: 'Status' }, { key: 'source', label: 'Source' }, { key: 'event', label: 'Events' },
    { key: 'cost', label: 'Cost' }, { key: 'time', label: 'Updated at' }, { key: 'runId', label: 'Run ID' },
  ];
  const [hidden, setHidden] = useState([]);
  const columns = allColumns.filter((c) => !hidden.includes(c.key));
  const filterGroups = [
    { key: 'status', label: 'Status', options: RUN_STATUSES },
    { key: 'source', label: 'Source', options: ['trigger', 'backfill'] },
    { key: 'outcome', label: 'Event', options: ['event', 'no event'], values: ['event', 'none'] },
  ];
  const filtered = Boolean(filters.status || filters.source || filters.outcome || q);

  return (
    <Stack gap="sm">
      <LoadError error={error ?? counts.error} />
      <Toolbar filterGroups={filterGroups} params={params} update={update} columns={allColumns} hidden={hidden}
        setHidden={setHidden} onRefresh={onRefresh} />
      <SearchBox value={search} onChange={(v) => { setSearch(v); if (page) update({ pageNumber: null }); }}
        placeholder="Search by status, run id, trace id, error…" />
      <Paper withBorder p="md">
        <TimeBarChart data={rows} series={series} minutes={minutes} markers={[]} h={170} />
        <Text size="sm" c="dimmed" mt="xs">Total: {total.toLocaleString()}</Text>
      </Paper>

      <Table.ScrollContainer minWidth={900}>
        <Table highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>{columns.map((c) => <Table.Th key={c.key}>{c.label}</Table.Th>)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pageRows.length === 0 && (
              <Table.Tr><Table.Td colSpan={columns.length}>
                <Text ta="center" c="dimmed" py="md">
                  {loading && !data ? 'Loading…' : !filtered ? 'No runs in this time range' : 'No runs match these filters'}
                </Text>
              </Table.Td></Table.Tr>
            )}
            {pageRows.map((r) => <Table.Tr key={r.id}>{columns.map((c) => <Table.Td key={c.key}>{runCell(c, r)}</Table.Td>)}</Table.Tr>)}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Paging total={total} page={page} update={update} />
    </Stack>
  );
}

function runCell(column, r) {
  switch (column.key) {
    case 'status':
      if (r.status === 'failed') return <Tooltip label={r.error ?? 'Failed'} multiline w={320}><Text size="sm" c="red.5">✕ Failed</Text></Tooltip>;
      if (r.status === 'completed') return <Text size="sm" c="dimmed">✓ Completed</Text>;
      return <Text size="sm" c="dimmed">⋯ {r.status === 'running' ? 'Running' : 'Pending'}</Text>;
    case 'source': return <Text size="sm">{r.source === 'backfill' ? 'Backfill' : 'Trigger'}</Text>;
    case 'event': return r.events
      ? <Text size="sm">{plural(r.events, 'event')}</Text>
      : r.laminarUrl
        ? <Anchor size="sm" c="dimmed" href={r.laminarUrl} target="_blank" rel="noreferrer">No event ↗</Anchor>
        : <Text size="sm" c="dimmed">No event</Text>;
    case 'cost': return <Text size="sm" ff="monospace">{r.cost == null ? '—' : formatCost(r.cost)}</Text>;
    case 'time': return <Text size="sm" style={{ whiteSpace: 'nowrap' }}>{formatTime(r.time)}</Text>;
    case 'runId': return <Text size="sm" ff="monospace" c="dimmed">{r.id}</Text>;
    default: return null;
  }
}

// ---------------------------------------------------------------- Settings tab
// Laminar's section menu (General / Versions / Alerts / Backfill). General is the shared form in edit
// mode, saving through the API; the others stay hidden until they have endpoints.

const SECTIONS = [
  { id: 'general', label: 'General', shown: true },
  { id: 'versions', label: 'Versions', shown: FEATURES.versionsTab },
  { id: 'alerts', label: 'Alerts', shown: FEATURES.alertsTab },
  { id: 'backfill', label: 'Backfill', shown: FEATURES.backfill },
].filter((x) => x.shown);

function SettingsTab({ tripwire, params, update, onSaved }) {
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
          ? <TripwireForm mode="edit" initialValues={tripwire} onTest={testRun}
              onSubmit={(values) => editSignal(tripwire.id, values).then(onSaved)} />
          : <Text c="dimmed">Placeholder</Text>}
      </Stack>
    </Group>
  );
}

// ---------------------------------------------------------------- shared bits of this page

// Add filter · Columns · Refresh, plus the active filters as removable chips.
function Toolbar({ filterGroups, params, update, columns, hidden, setHidden, onRefresh }) {
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
      <Button variant="default" size="xs" onClick={onRefresh}>↻ Refresh</Button>
      {active.map((g) => {
        const value = params.get(g.key);
        const label = (g.values ? g.options[g.values.indexOf(value)] : value) ?? value;
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

// Side panel for one event: its fields, evidence, ids, and a link to the trace in Laminar.
function EventDrawer({ tripwire, event, now, update }) {
  return (
    <Drawer opened={Boolean(event)} onClose={() => update({ event: null, pageNumber: undefined })} position="right" size="lg"
      title={<Group gap="xs"><Text fw={600}>Event</Text>{event && <SeverityBadge severity={event.severity} />}</Group>}>
      {event && (
        <Stack gap="md">
          <Text size="sm" c="dimmed">{formatTime(event.time)} · {formatRelative(event.time, now)}</Text>

          {tripwire.payloadFields.map((f) => (
            <Box key={f.name}>
              <Label>{f.name}</Label>
              <Text size="sm">{String(event.payload[f.name] ?? '—')}</Text>
            </Box>
          ))}

          <Box>
            <Label>Conversation turn</Label>
            <Text size="sm" c="dimmed">Open the trace in Laminar to see the conversation.</Text>
          </Box>

          <Table verticalSpacing={4}>
            <Table.Tbody>
              {[['Event ID', event.id], ['Run ID', event.runId], ['Trace ID', event.traceId],
                ['Conversation', event.conversationId ?? '—'], ['Evidence', event.evidenceLabels.join(', ') || '—'],
                ['Signal version', event.versionNumber]].map(([k, v]) => (
                <Table.Tr key={k}><Table.Td><Text size="xs" c="dimmed">{k}</Text></Table.Td><Table.Td><Text size="xs" ff="monospace">{v}</Text></Table.Td></Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          {event.laminarUrl && (
            <Group><Button component="a" href={event.laminarUrl} target="_blank" rel="noreferrer">Open trace in Laminar ↗</Button></Group>
          )}
        </Stack>
      )}
    </Drawer>
  );
}

function Label({ children }) {
  return <Text size="xs" c="dimmed" tt="uppercase" fw={600} mb={2} style={{ letterSpacing: '0.04em' }}>{children}</Text>;
}
