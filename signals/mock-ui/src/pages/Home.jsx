import { Link, useSearchParams } from 'react-router-dom';
import { Anchor, Box, Button, Group, Paper, Select, SimpleGrid, Stack, Text, Title, UnstyledButton } from '@mantine/core';
import { useStoryData } from '../components/StoryClock';
import { ToggleLineChart } from '../components/Charts';
import TimeRangePicker from '../components/TimeRangePicker';
import { formatCost, plural } from '../components/format';
import { tripwireUrl, tripwireListUrl } from '../components/links';
import { rangeFromParams, makeBuckets, bucketIndex } from '../components/timeRange';
import { clusterColor, UNCLUSTERED_COLOR, RUN_COLORS } from '../theme';

// Home: a fixed dashboard for signals, built from Laminar's dashboard card types
// (ranked lists and charts over time). Not customizable.
// Address: /?pastHours=168&granularity=hour|day

const DEFAULT_HOURS = 24; // past 24 hours
const TOP = 5;

export default function Home() {
  const [params, setParams] = useSearchParams();
  const data = useStoryData();
  const range = rangeFromParams(params, data.asOf, DEFAULT_HOURS);
  // By hour for ranges up to 7 days, by day for longer ones, unless chosen.
  const granularity = ['hour', 'day'].includes(params.get('granularity')) ? params.get('granularity') : (range.hours <= 168 ? 'hour' : 'day');
  const minutes = granularity === 'hour' ? 60 : 1440;
  const buckets = makeBuckets(range.start, range.end, minutes);
  const setGranularity = (g) => { const next = new URLSearchParams(params); next.set('granularity', g); setParams(next); };

  const inRange = (x) => x.time >= range.start && x.time <= range.end;
  const events = data.tripwireEvents.filter(inRange);
  const runs = data.runs.filter(inRange);
  const nameOf = (id) => data.getTripwire(id)?.name ?? id;

  // Top N by a number; ties broken by name; zeros left out.
  const top = (items, value, label) => items
    .map((x) => ({ ...x, value: value(x) })).filter((x) => x.value > 0)
    .sort((a, b) => b.value - a.value || label(a).localeCompare(label(b)));

  // "Problem" = critical or warning. Counting only these keeps signals that produce an event for every
  // trace (Intent Classifier, Task Evaluator's successes) from turning the event cards into a traffic count.
  const isProblem = (e) => e.severity === 'critical' || e.severity === 'warning';
  const problems = events.filter(isProblem);
  const byProblems = top(data.tripwires, (t) => problems.filter((e) => e.tripwireId === t.id).length, (t) => t.name);
  const byCluster = top(data.clusters.filter((c) => c.parentId), (c) => problems.filter((e) => e.clusterId === c.id).length, (c) => c.name);
  const byCost = top(data.tripwires, (t) => runs.filter((r) => r.tripwireId === t.id).reduce((sum, r) => sum + r.cost, 0), (t) => t.name);
  const totalCost = runs.reduce((sum, r) => sum + r.cost, 0);

  // Intent breakdown: only when a signal created from the Intent template exists (survives renames).
  const intentSignal = data.tripwires.find((t) => t.templateId === 'intent');
  const intentEvents = intentSignal ? events.filter((e) => e.tripwireId === intentSignal.id) : [];
  const byIntent = intentSignal
    ? top(data.leafClusters(intentSignal.id), (c) => intentEvents.filter((e) => e.clusterId === c.id).length, (c) => c.name)
    : [];
  const pct = (n) => `${Math.round((n / intentEvents.length) * 100)}%`;

  // Chart rows: one per bucket; top 5 signals get their own line, the rest are summed into "Other".
  const perSignalSeries = (ranked, items) => {
    const shown = ranked.slice(0, TOP);
    // A signal keeps one color on every card (by its place in the signal list, not its rank in this chart).
    const series = shown.map((t) => ({ name: t.id, label: t.name, color: clusterColor(data.tripwires.findIndex((x) => x.id === t.id)) }));
    const hasOther = ranked.length > TOP;
    if (hasOther) series.push({ name: 'other', label: 'Other', color: UNCLUSTERED_COLOR });
    const rows = buckets.map((b) => Object.fromEntries([['bucket', b.start], ...series.map((s) => [s.name, 0])]));
    for (const x of items) {
      const i = bucketIndex(buckets, x.time);
      if (i < 0) continue;
      const key = shown.some((t) => t.id === x.tripwireId) ? x.tripwireId : (hasOther ? 'other' : null);
      if (key) rows[i][key] += 1;
    }
    return { series, rows, shown };
  };
  const problemsChart = perSignalSeries(byProblems, problems);
  // Legend link for a signal line ("Other" has no page).
  const signalLink = (series) => (series.name === 'other' ? null : tripwireUrl(series.name, { range }));
  const runSeries = [
    { name: 'event', label: 'Found an event', color: RUN_COLORS.event },
    { name: 'noEvent', label: 'Found nothing', color: RUN_COLORS.noEvent },
    { name: 'failed', label: 'Failed', color: RUN_COLORS.failed,
      description: "Couldn't finish: model error, unusable output, or the trace couldn't be read." },
  ];
  const runRows = buckets.map((b) => ({ bucket: b.start, event: 0, noEvent: 0, failed: 0 }));
  for (const r of runs) {
    const i = bucketIndex(buckets, r.time);
    if (i >= 0) runRows[i][r.status === 'failed' ? 'failed' : r.eventId ? 'event' : 'noEvent'] += 1;
  }

  return (
    <Stack gap="md">
      <Group gap="sm" wrap="wrap">
        <Title order={3}>Home</Title>
        <TimeRangePicker defaultHours={DEFAULT_HOURS} />
        <Select id="home-granularity" size="xs" w={110} allowDeselect={false} value={granularity} onChange={setGranularity}
          data={[{ value: 'hour', label: 'By hour' }, { value: 'day', label: 'By day' }]} aria-label="Chart granularity" />
        <Button variant="default" size="xs">↻ Refresh</Button>
      </Group>

      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <Card title="Problem events by signal" note="Critical and warning events" total={problems.length}
          footer={<Anchor component={Link} to={tripwireListUrl()} size="sm">View all signals →</Anchor>}>
          <RankedList rows={byProblems.slice(0, TOP).map((t) => ({ key: t.id, label: t.name, value: t.value, to: tripwireUrl(t.id, { range }) }))} />
        </Card>
        <Card title="Top problem clusters" note="Ranked by critical and warning events" total={byCluster.length} totalLabel={plural(byCluster.length, 'cluster')}>
          <RankedList rows={byCluster.slice(0, TOP).map((c) => ({
            key: c.id, label: c.name, sub: nameOf(c.tripwireId), value: c.value, to: tripwireUrl(c.tripwireId, { clusterId: c.id, range }),
          }))} />
        </Card>
        <Card title="Signal run cost" totalLabel={formatCost(totalCost)}>
          <RankedList rows={byCost.slice(0, TOP).map((t) => ({ key: t.id, label: t.name, value: t.value, display: formatCost(t.value), to: tripwireUrl(t.id, { tab: 'runs', range }) }))} />
        </Card>
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <Card title="Problem events over time" note="Critical and warning events">
          {problems.length === 0
            ? <Empty text="No problem events in this time range." />
            : <ToggleLineChart data={problemsChart.rows} series={problemsChart.series} minutes={minutes} linkFor={signalLink} />}
        </Card>
        {intentSignal && (
          <Card title="What visitors asked for" note={`From ${intentSignal.name}`} total={intentEvents.length} totalLabel={plural(intentEvents.length, 'conversation turn')}>
            <RankedList rows={byIntent.slice(0, TOP).map((c) => ({
              key: c.id, label: c.name, value: c.value, display: `${c.value} · ${pct(c.value)}`,
              to: tripwireUrl(intentSignal.id, { clusterId: c.id, range }),
            }))} />
          </Card>
        )}
        <Card title="Signal runs (all signals)" note="Each run is one signal reading one trace" total={runs.length} totalLabel={plural(runs.length, 'run')}>
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
