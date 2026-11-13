import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Anchor, Box, Button, Group, Paper, Select, SimpleGrid, Stack, Text, Title, UnstyledButton } from '@mantine/core';
import { useOverview } from '../api';
import { ToggleLineChart } from '../components/Charts';
import TimeRangePicker from '../components/TimeRangePicker';
import { formatCost, plural } from '../components/format';
import { tripwireUrl, tripwireListUrl } from '../components/links';
import { rangeFromParams, makeBuckets, bucketIndex } from '../components/timeRange';
import { clusterColor, UNCLUSTERED_COLOR, RUN_COLORS } from '../theme';

// Home: a fixed dashboard for signals, built from Laminar's dashboard card types
// (ranked lists and charts over time). Not customizable. Reads /api/overview: every number is
// counted in the database, by when the turn happened.
// Address: /?pastHours=168&granularity=hour|day

const DEFAULT_HOURS = 24; // past 24 hours
const TOP = 5;

// "Now" in the pages' time format (UTC, no zone). Read once, and again when Refresh is clicked.
const currentTime = () => new Date().toISOString().slice(0, 19);

export default function Home() {
  const [params, setParams] = useSearchParams();
  const [now, setNow] = useState(currentTime);
  const range = rangeFromParams(params, now, DEFAULT_HOURS);
  // By hour for ranges up to 7 days, by day for longer ones, unless chosen.
  const granularity = ['hour', 'day'].includes(params.get('granularity')) ? params.get('granularity') : (range.hours <= 168 ? 'hour' : 'day');
  const minutes = granularity === 'hour' ? 60 : 1440;
  const buckets = makeBuckets(range.start, range.end, minutes);
  const setGranularity = (g) => { const next = new URLSearchParams(params); next.set('granularity', g); setParams(next); };
  const { data, error, loading } = useOverview(range, minutes);
  const overview = data ?? { signals: [], problemClusters: [], problemCounts: [], runCounts: [] };

  // Top N by a number; ties broken by name; zeros left out.
  const top = (items, value, label) => items
    .map((x) => ({ ...x, value: value(x) })).filter((x) => x.value > 0)
    .sort((a, b) => b.value - a.value || label(a).localeCompare(label(b)));

  // "Problem" = critical or warning. Counting only these keeps signals that produce an event for every
  // trace from turning the event cards into a traffic count.
  const signals = overview.signals;
  const byProblems = top(signals, (s) => s.problemEvents, (s) => s.name);
  const totalProblems = byProblems.reduce((sum, s) => sum + s.value, 0);
  const byCost = top(signals, (s) => s.cost, (s) => s.name);
  const totalCost = signals.reduce((sum, s) => sum + s.cost, 0);
  const unpriced = signals.reduce((sum, s) => sum + s.unpricedRuns, 0);

  // Problem events over time: the top 5 signals get their own line, the rest are summed into "Other".
  const shown = byProblems.slice(0, TOP);
  // A signal keeps one color on every card (by its place in the signal list, not its rank in this chart).
  const problemSeries = shown.map((s) => ({ name: String(s.id), label: s.name, color: clusterColor(signals.findIndex((x) => x.id === s.id)) }));
  if (byProblems.length > TOP) problemSeries.push({ name: 'other', label: 'Other', color: UNCLUSTERED_COLOR });
  const problemRows = buckets.map((b) => Object.fromEntries([['bucket', b.start], ...problemSeries.map((x) => [x.name, 0])]));
  for (const c of overview.problemCounts) {
    const i = bucketIndex(buckets, c.bucket);
    if (i < 0) continue;
    const key = shown.some((s) => s.id === c.signalId) ? String(c.signalId) : 'other';
    if (key in problemRows[i]) problemRows[i][key] += c.count;
  }
  // Legend link for a signal line ("Other" has no page).
  const signalLink = (series) => (series.name === 'other' ? null : tripwireUrl(series.name, { range }));

  // Finished runs only: a run still waiting or in progress isn't counted until it finishes.
  const runSeries = [
    { name: 'event', label: 'Found an event', color: RUN_COLORS.event },
    { name: 'noEvent', label: 'Found nothing', color: RUN_COLORS.noEvent },
    { name: 'failed', label: 'Failed', color: RUN_COLORS.failed,
      description: "Couldn't finish: model error, unusable output, or the trace couldn't be read." },
  ];
  const runRows = buckets.map((b) => ({ bucket: b.start, event: 0, noEvent: 0, failed: 0 }));
  let finishedRuns = 0;
  for (const c of overview.runCounts) {
    const i = bucketIndex(buckets, c.bucket);
    if (i < 0) continue;
    for (const k of ['event', 'noEvent', 'failed']) { runRows[i][k] += c[k]; finishedRuns += c[k]; }
  }

  return (
    <Stack gap="md">
      <Group gap="sm" wrap="wrap">
        <Title order={3}>Home</Title>
        <TimeRangePicker defaultHours={DEFAULT_HOURS} now={now} />
        <Select id="home-granularity" size="xs" w={110} allowDeselect={false} value={granularity} onChange={setGranularity}
          data={[{ value: 'hour', label: 'By hour' }, { value: 'day', label: 'By day' }]} aria-label="Chart granularity" />
        <Button variant="default" size="xs" loading={loading && Boolean(data)} onClick={() => setNow(currentTime())}>↻ Refresh</Button>
      </Group>
      {error && <Alert color="red" title="Couldn't load">{error.message}</Alert>}
      {loading && !data && <Text c="dimmed">Loading…</Text>}

      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <Card title="Problem events by signal" note="Critical and warning events" total={totalProblems}
          footer={<Anchor component={Link} to={tripwireListUrl()} size="sm">View all signals →</Anchor>}>
          <RankedList rows={byProblems.slice(0, TOP).map((s) => ({ key: s.id, label: s.name, value: s.value, to: tripwireUrl(s.id, { range }) }))} />
        </Card>
        <Card title="Top problem clusters" note="Ranked by critical and warning events" total={overview.problemClusters.length}
          totalLabel={plural(overview.problemClusters.length, 'cluster')}>
          <RankedList rows={overview.problemClusters.slice(0, TOP).map((c) => ({
            key: c.id, label: c.name, sub: c.signalName, value: c.problemEvents, to: tripwireUrl(c.signalId, { clusterId: c.id, range }),
          }))} />
        </Card>
        <Card title="Signal run cost" note={unpriced ? `${plural(unpriced, 'run')} without a known price not included` : undefined}
          totalLabel={formatCost(totalCost)}>
          <RankedList rows={byCost.slice(0, TOP).map((s) => ({ key: s.id, label: s.name, value: s.value, display: formatCost(s.value), to: tripwireUrl(s.id, { tab: 'runs', range }) }))} />
        </Card>
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Card title="Problem events over time" note="Critical and warning events">
          {totalProblems === 0
            ? <Empty text="No problem events in this time range." />
            : <ToggleLineChart data={problemRows} series={problemSeries} minutes={minutes} linkFor={signalLink} />}
        </Card>
        <Card title="Signal runs (all signals)" note="Finished runs; each is one signal reading one trace" total={finishedRuns}
          totalLabel={plural(finishedRuns, 'run')}>
          <ToggleLineChart data={runRows} series={runSeries} minutes={minutes} />
        </Card>
      </SimpleGrid>
    </Stack>
  );
}

// A dashboard card: title, optional note, big number, body, optional footer.
function Card({ title, note, total, totalLabel, footer, children }) {
  return (
    <Paper withBorder p="md" style={{ minWidth: 0 }}>
      <Text fw={600}>{title}</Text>
      {note && <Text size="xs" c="dimmed">{note}</Text>}
      {(total !== undefined || totalLabel) && <Title order={2} mb="xs">{totalLabel ?? total.toLocaleString()}</Title>}
      {children}
      {footer && <Box mt="sm">{footer}</Box>}
    </Paper>
  );
}

// Laminar's "Top spans" style: a bar behind each label, sized by its value, with the value on the right.
function RankedList({ rows }) {
  if (rows.length === 0) return <Empty text="Nothing in this time range." />;
  const max = Math.max(...rows.map((r) => r.value));
  return (
    <Stack gap={6}>
      {rows.map((r) => (
        <UnstyledButton key={r.key} component={Link} to={r.to} aria-label={`${r.label}: ${r.display ?? r.value}`}>
          <Group gap="sm" wrap="nowrap">
            <Box style={{ flex: 1, minWidth: 0, position: 'relative', borderRadius: 'var(--mantine-radius-sm)', overflow: 'hidden' }}>
              <Box style={{ position: 'absolute', inset: 0, width: `${(r.value / max) * 100}%`, background: 'var(--mantine-color-dark-5)' }} />
              <Box px="xs" py={6} style={{ position: 'relative' }}>
                <Text size="sm" truncate="end">{r.label}</Text>
                {r.sub && <Text size="xs" c="dimmed" truncate="end">{r.sub}</Text>}
              </Box>
            </Box>
            <Text size="sm" style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{r.display ?? r.value.toLocaleString()}</Text>
          </Group>
        </UnstyledButton>
      ))}
    </Stack>
  );
}

function Empty({ text }) {
  return <Text size="sm" c="dimmed" py="md">{text}</Text>;
}
