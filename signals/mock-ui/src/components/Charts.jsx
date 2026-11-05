import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Anchor, Box, Paper, Text, Group, Stack, ColorSwatch, Tooltip, UnstyledButton } from '@mantine/core';
import { AreaChart, BarChart, LineChart, Sparkline } from '@mantine/charts';
import { formatTime } from './format';

// Small trend line for tables and cards. `data` is a list of numbers (one per day).
export function ActivitySparkline({ data, w = 100, h = 28 }) {
  return <Sparkline w={w} h={h} data={data} curveType="monotone" color="teal" fillOpacity={0.15} />;
}

// Signal events per day. `data` comes from useStoryData().eventsPerDay().
export function EventsOverTimeChart({ data, h = 220 }) {
  return (
    <Paper withBorder p="md">
      <AreaChart h={h} data={data} dataKey="day" curveType="monotone" gridAxis="y"
        series={[{ name: 'events', label: 'Events', color: 'teal.6' }]} />
    </Paper>
  );
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Axis label for a bucket start, depending on bar size (like Laminar: "Thu 29", "12 PM", "Aug 09").
// compact (narrow charts): fewer labels: days only for hourly bars, every 14th day for daily bars.
function tickLabel(iso, minutes, i, compact = false) {
  const [date, time] = iso.split('T');
  const [y, mo, d] = date.split('-').map(Number);
  const [hh, mm] = time.split(':').map(Number);
  const dow = new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
  if (minutes >= 1440) return i % (compact ? 14 : 7) === 0 ? `${MONTHS[mo - 1]} ${String(d).padStart(2, '0')}` : '';
  if (minutes >= 360) return hh === 0 ? `${WEEKDAYS[dow]} ${d}` : '';
  if (minutes >= 60) {
    if (hh === 0 && mm === 0) return `${WEEKDAYS[dow]} ${d}`;
    return !compact && hh === 12 && mm === 0 ? '12 PM' : '';
  }
  // Short ranges: a label every hour (or every 15 minutes for 1-minute bars).
  const every = (minutes <= 1 ? 15 : 60) * (compact ? 2 : 1);
  if ((hh * 60 + mm) % every !== 0) return '';
  if (hh === 0 && mm === 0) return `${WEEKDAYS[dow]} ${d}`;
  const h12 = hh % 12 || 12;
  return `${h12}${mm ? `:${String(mm).padStart(2, '0')}` : ''} ${hh >= 12 ? 'PM' : 'AM'}`;
}

// Stacked bars over time (Events and Runs tabs).
//   data:    one row per bucket: { bucket: <bucket start ISO>, <series name>: count, ... }
//   series:  [{ name, label, color }] stacked bottom to top
//   minutes: bucket size, for the axis labels
//   markers: [{ at: <bucket start ISO>, label: 'v1.4.1' }] drawn as vertical lines (app versions).
//            A label that would crowd the previous one is left off (the line still shows).
export function TimeBarChart({ data, series, minutes, markers = [], h = 200 }) {
  const ticks = data.map((row, i) => ({ v: row.bucket, label: tickLabel(row.bucket, minutes, i) }));
  const labelOf = Object.fromEntries(ticks.map((t) => [t.v, t.label]));
  return (
    <BarChart h={h} data={data} dataKey="bucket" type="stacked" series={series} gridAxis="x" withLegend={false}
      barProps={{ maxBarSize: 24 }}
      xAxisProps={{ interval: 0, tickLine: false, tickFormatter: (v) => labelOf[v] ?? '', tick: { fontSize: 12, fill: 'var(--mantine-color-dimmed)' } }}
      yAxisProps={{ allowDecimals: false, tick: { fontSize: 12, fill: 'var(--mantine-color-dimmed)' } }}
      referenceLines={spacedMarkers(markers, data).map((m) => ({ x: m.at, label: m.label ? `▼ ${m.label}` : undefined, color: 'gray.6', labelPosition: 'insideTopLeft' }))}
      tooltipProps={{ content: ({ label, payload }) => <BucketTooltip label={label} payload={payload} series={series} /> }} />
  );
}

// Lines over time (Home dashboard cards, like Laminar's dashboard charts). Same data shape as TimeBarChart.
export function TimeLineChart({ data, series, minutes, h = 180 }) {
  const ticks = data.map((row, i) => ({ v: row.bucket, label: tickLabel(row.bucket, minutes, i, true) }));
  const labelOf = Object.fromEntries(ticks.map((t) => [t.v, t.label]));
  return (
    <LineChart h={h} data={data} dataKey="bucket" series={series} gridAxis="x" withLegend={false} withDots={false}
      curveType="linear" strokeWidth={1.5}
      xAxisProps={{ interval: 0, tickLine: false, tickFormatter: (v) => labelOf[v] ?? '', tick: { fontSize: 12, fill: 'var(--mantine-color-dimmed)' } }}
      yAxisProps={{ allowDecimals: false, tick: { fontSize: 12, fill: 'var(--mantine-color-dimmed)' } }}
      tooltipProps={{ content: ({ label, payload }) => <BucketTooltip label={label} payload={payload} series={series} /> }} />
  );
}

// TimeLineChart plus a legend whose items show or hide their line. The y-axis rescales to the lines still shown,
// so hiding the busiest series makes the small ones readable. Hidden lines reset on reload.
//   linkFor (optional): series -> a link (e.g. to that signal), shown as a small ↗ after the name.
//   A series' `description` (optional) shows as a tooltip on its legend item.
export function ToggleLineChart({ data, series, minutes, h = 180, linkFor }) {
  const [hidden, setHidden] = useState([]);
  const visible = series.filter((s) => !hidden.includes(s.name));
  const toggle = (name) => setHidden((cur) => (cur.includes(name) ? cur.filter((n) => n !== name) : [...cur, name]));
  return (
    <>
      {visible.length > 0
        ? <TimeLineChart data={data} series={visible} minutes={minutes} h={h} />
        : (
          <Box h={h} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Text size="sm" c="dimmed" ta="center">All lines hidden. Click a legend item to show it.</Text>
          </Box>
        )}
      <Group gap="sm" mt="xs">
        {series.map((s) => {
          const off = hidden.includes(s.name);
          const to = linkFor?.(s);
          return (
            <Group key={s.name} gap={4} wrap="nowrap">
              <Tooltip label={s.description} disabled={!s.description} multiline w={240} withArrow>
                <UnstyledButton onClick={() => toggle(s.name)} aria-pressed={!off} aria-label={`${off ? 'Show' : 'Hide'} ${s.label}`}>
                  <Group gap={6} wrap="nowrap" style={{ opacity: off ? 0.45 : 1 }}>
                    <ColorSwatch size={10} radius="xs" color={`var(--mantine-color-${s.color.replace('.', '-')})`} withShadow={false} />
                    <Text size="xs" td={off ? 'line-through' : undefined}>{s.label}</Text>
                  </Group>
                </UnstyledButton>
              </Tooltip>
              {to && <Anchor component={Link} to={to} size="xs" aria-label={`Open ${s.label}`}>↗</Anchor>}
            </Group>
          );
        })}
      </Group>
    </>
  );
}

function BucketTooltip({ label, payload, series }) {
  if (!payload?.length) return null;
  const rows = series.map((s) => ({ ...s, value: payload.find((p) => p.dataKey === s.name)?.value ?? 0 })).filter((r) => r.value > 0);
  return (
    <Paper withBorder p="xs" shadow="md">
      <Text size="xs" c="dimmed" mb={4}>{formatTime(label)}</Text>
      {rows.length === 0 ? <Text size="xs">None</Text> : (
        <Stack gap={2}>
          {rows.map((r) => (
            <Group key={r.name} gap={6} wrap="nowrap">
              <ColorSwatch size={10} color={`var(--mantine-color-${r.color.replace('.', '-')})`} />
              <Text size="xs" style={{ flex: 1 }}>{r.label}</Text>
              <Text size="xs" fw={600}>{r.value}</Text>
            </Group>
          ))}
        </Stack>
      )}
    </Paper>
  );
}

// Leave a marker's label off when it sits within 8% of the chart of the previous labeled marker.
function spacedMarkers(markers, data) {
  const index = Object.fromEntries(data.map((row, i) => [row.bucket, i]));
  const gap = Math.max(1, Math.round(data.length * 0.08));
  let last = -Infinity;
  return markers.map((m) => {
    const i = index[m.at] ?? 0;
    if (i - last < gap) return { ...m, label: null };
    last = i;
    return m;
  });
}
