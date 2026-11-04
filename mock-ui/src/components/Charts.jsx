import { Paper } from '@mantine/core';
import { AreaChart, Sparkline } from '@mantine/charts';

// Small trend line for tables and cards. `data` is a list of numbers (one per day).
export function ActivitySparkline({ data, w = 100, h = 28 }) {
  return <Sparkline w={w} h={h} data={data} curveType="monotone" color="teal" fillOpacity={0.15} />;
}

// Tripwire events per day. `data` comes from eventsPerDay() in mock/data.js.
export function EventsOverTimeChart({ data, h = 220 }) {
  return (
    <Paper withBorder p="md">
      <AreaChart h={h} data={data} dataKey="day" curveType="monotone" gridAxis="y"
        series={[{ name: 'events', label: 'Tripwire events', color: 'teal.6' }]} />
    </Paper>
  );
}
