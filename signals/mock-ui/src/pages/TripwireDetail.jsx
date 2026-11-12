import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  Alert, Anchor, Badge, Box, Breadcrumbs, Button, Checkbox, CloseButton, Drawer, Group, Kbd, Menu, Pagination, Paper,
  SegmentedControl, Stack, Table, Text, TextInput, Title, Tooltip, UnstyledButton,
} from '@mantine/core';
import { FEATURES, editSignal, setActive, testRun, useEvents, useRuns, useSignal } from '../api';
import { SeverityBadge, KindBadge, StatusBadge } from '../components/Badges';
import { TimeBarChart } from '../components/Charts';
import TimeRangePicker from '../components/TimeRangePicker';
import PageHeader from '../components/PageHeader';
import TripwireForm from '../components/TripwireForm';
import { formatTime, formatRelative, formatCost, shortId, plural } from '../components/format';
import { tripwireListUrl } from '../components/links';
import { rangeFromParams, bucketMinutesFor, makeBuckets, bucketIndex, shortLabel } from '../components/timeRange';
import { cssColor, UNCLUSTERED_COLOR, RUN_COLORS } from '../theme';

// A signal's page, modeled on Laminar's Signal page: Events, Runs and Settings tabs with a time range.
// Address: /signals/<id>?tab=&event=&pastHours=&pageNumber=&severity=...
// Reads the real API. Clusters and app versions are hidden until they have endpoints (src/api/features.js).

const PAGE_SIZE = 25;
const DEFAULT_HOURS = 72; // a signal's page opens on the past 3 days
const SEVERITIES = ['critical', 'warning', 'info'];

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

function EventsTab({ tripwire, params, update, now, onRefresh }) {
  const range = rangeFromParams(params, now, DEFAULT_HOURS);
  const { data, error, loading } = useEvents(tripwire.id, range);
  const inRange = data ?? [];

  // Events shown: in range, matching filters and search.
  const severity = params.get('severity');
  const [search, setSearch] = useState('');
  const shown = inRange.filter((e) => {
    if (severity && e.severity !== severity) return false;
    if (search) {
      const hay = [e.severity, e.traceId, e.id, ...e.evidenceLabels, ...Object.values(e.payload)].join(' ').toLowerCase();
      if (!hay.includes(search.toLowerCase())) return false;
    }
    return true;
  });

  // Chart: one series until clusters have an endpoint; then one series per cluster (FEATURES.clusterCounts).
  const minutes = bucketMinutesFor(range.hours);
  const buckets = makeBuckets(range.start, range.end, minutes);
  const series = [{ name: 'events', label: 'Events', color: UNCLUSTERED_COLOR }];
  const rows = buckets.map((b) => ({ bucket: b.start, events: 0 }));
  for (const e of inRange) {
    const i = bucketIndex(buckets, e.time);
    if (i >= 0) rows[i].events += 1;
  }

  // Table columns: time, severity, the signal's own fields, trace, id.
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
  const openEvent = inRange.find((e) => String(e.id) === openEventId) ?? null;

  const filterGroups = [{ key: 'severity', label: 'Severity', options: SEVERITIES }];

  return (
    <Stack gap="sm">
      {FEATURES.clusterCounts && <ClusterStrip clusters={[]} onSelect={(c) => update({ cluster: c.id, event: null })} />}
      <LoadError error={error} />

      <Paper withBorder p="md">
        <TimeBarChart data={rows} series={series} minutes={minutes} markers={[]} h={190} />
      </Paper>

      <Toolbar filterGroups={filterGroups} params={params} update={update} columns={allColumns} hidden={hidden}
        setHidden={setHidden} onRefresh={onRefresh} />
      <SearchBox value={search} onChange={setSearch} placeholder="Search events by fields, severity, trace id, evidence…" />

      <Table.ScrollContainer minWidth={900}>
        <Table highlightOnHover verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>{columns.map((c) => <Table.Th key={c.key} w={c.w}>{c.label}</Table.Th>)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pageRows.length === 0 && (
              <Table.Tr><Table.Td colSpan={columns.length}>
                <Text ta="center" c="dimmed" py="md">
                  {loading ? 'Loading…'
                    : inRange.length === 0 ? `No events in the last ${range.absolute ? 'selected range' : shortLabel(range.hours)}`
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
      <Paging total={shown.length} page={page} update={update} />

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

// Colored bars, one per cluster, sized by event count (Laminar's cluster strip). Shown when
// FEATURES.clusterCounts is on and the clusters endpoint fills `clusters`.
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

// ---------------------------------------------------------------- Runs tab

const RUN_STATUSES = ['completed', 'failed', 'pending', 'running'];

function RunsTab({ tripwire, params, update, now, onRefresh }) {
  const range = rangeFromParams(params, now, DEFAULT_HOURS);
  const { data, error, loading } = useRuns(tripwire.id, range);
  const inRange = data ?? [];
  const [search, setSearch] = useState('');
  const status = params.get('status');
  const source = params.get('source');
  const outcome = params.get('outcome');
  // Unfinished runs (pending, running) aren't in the chart yet.
  const outcomeOf = (r) => (r.status === 'failed' ? 'failed' : r.status !== 'completed' ? null : r.events ? 'event' : 'noEvent');

  const shown = inRange.filter((r) => {
    if (status && r.status !== status) return false;
    if (source && r.source !== source) return false;
    if (outcome && (outcome === 'event') !== r.events > 0) return false;
    if (search) {
      const hay = [r.status, r.source, r.id, r.traceId, r.error ?? ''].join(' ').toLowerCase();
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
  for (const r of shown) {
    const i = bucketIndex(buckets, r.time);
    const o = outcomeOf(r);
    if (i >= 0 && o) rows[i][o] += 1;
  }

  const allColumns = [
    { key: 'status', label: 'Status' }, { key: 'source', label: 'Source' }, { key: 'event', label: 'Events' },
    ...(FEATURES.clusterCounts ? [{ key: 'clusters', label: 'Clusters' }] : []),
    { key: 'cost', label: 'Cost' }, { key: 'time', label: 'Updated at' }, { key: 'runId', label: 'Run ID' },
  ];
  const [hidden, setHidden] = useState([]);
  const columns = allColumns.filter((c) => !hidden.includes(c.key));
  const page = Math.max(0, Number(params.get('pageNumber')) || 0);
  const pageRows = shown.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const filterGroups = [
    { key: 'status', label: 'Status', options: RUN_STATUSES },
    { key: 'source', label: 'Source', options: ['trigger', 'backfill'] },
    { key: 'outcome', label: 'Event', options: ['event', 'no event'], values: ['event', 'none'] },
  ];

  return (
    <Stack gap="sm">
      <LoadError error={error} />
      <Toolbar filterGroups={filterGroups} params={params} update={update} columns={allColumns} hidden={hidden}
        setHidden={setHidden} onRefresh={onRefresh} />
      <SearchBox value={search} onChange={setSearch} placeholder="Filter by status, run id, trace id, error…" />
      <Paper withBorder p="md">
        <TimeBarChart data={rows} series={series} minutes={minutes} markers={[]} h={170} />
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
                <Text ta="center" c="dimmed" py="md">
                  {loading ? 'Loading…' : inRange.length === 0 ? 'No runs in this time range' : 'No runs match these filters'}
                </Text>
              </Table.Td></Table.Tr>
            )}
            {pageRows.map((r) => <Table.Tr key={r.id}>{columns.map((c) => <Table.Td key={c.key}>{runCell(c, r)}</Table.Td>)}</Table.Tr>)}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Paging total={shown.length} page={page} update={update} />
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
    case 'clusters': return <Text size="sm" c="dimmed">-</Text>;
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
