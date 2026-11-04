import { Stack, Title, Text, Card, Group, Button, Table, SimpleGrid, TextInput, Textarea, Switch, Paper, Code } from '@mantine/core';
import PageHeader from '../components/PageHeader';
import { SeverityBadge, KindBadge, StatusBadge, NotificationTypeBadge } from '../components/Badges';
import { ActivitySparkline, EventsOverTimeChart } from '../components/Charts';
import LaminarLink from '../components/LaminarLink';
import { formatTime, formatDay } from '../components/format';
import {
  tripwires, tripwireSummary, eventsFor, eventsPerDay, topClusters, subClusters, eventsInCluster,
  getCluster, notifications, getTripwire,
} from '../mock/data';

// Reference page, not part of the product: every shared component with real mock data.
// Pages should look like this. Change theme.js or src/components/, never this page, to change the look.

function Section({ title, note, children }) {
  return (
    <Stack gap="sm">
      <div>
        <Title order={3}>{title}</Title>
        {note && <Text size="sm" c="dimmed">{note}</Text>}
      </div>
      {children}
    </Stack>
  );
}

export default function StyleSample() {
  const friction = 'friction-detector';
  const rows = tripwires.map((t) => tripwireSummary(t.id));
  const events = eventsFor(friction).slice(0, 4);

  return (
    <Stack gap="xl" maw={1100}>
      <PageHeader title="Style sample" description="Every shared component, with the shared mock data. Not a product screen." />

      <Section title="Buttons" note="Main action: filled. Secondary: light. Tertiary: subtle gray.">
        <Group>
          <Button>New tripwire</Button>
          <Button variant="light">Run on past traces</Button>
          <Button variant="subtle" color="gray">Cancel</Button>
        </Group>
      </Section>

      <Section title="Badges" note="SeverityBadge, KindBadge, StatusBadge, NotificationTypeBadge.">
        <Group>
          <SeverityBadge severity="critical" /><SeverityBadge severity="warning" /><SeverityBadge severity="info" />
          <KindBadge kind="preset" /><KindBadge kind="custom" />
          <StatusBadge status="active" />
          <NotificationTypeBadge type="new-cluster" /><NotificationTypeBadge type="critical-event" />
        </Group>
      </Section>

      <Section title="Tripwire cards" note="Card + ActivitySparkline (Home).">
        <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }}>
          {rows.filter((t) => ['friction-detector', 'failure-detector', 'made-up-park-facts'].includes(t.id)).map((t) => (
            <Card key={t.id} withBorder padding="lg">
              <Group justify="space-between" mb="xs">
                <Text fw={600}>{t.name}</Text>
                <KindBadge kind={t.kind} />
              </Group>
              <Group justify="space-between" align="flex-end">
                <div>
                  <Text size="xl" fw={700}>{t.events}</Text>
                  <Text size="xs" c="dimmed">tripwire events, last 7 days</Text>
                </div>
                <ActivitySparkline data={t.activity} w={110} h={40} />
              </Group>
            </Card>
          ))}
        </SimpleGrid>
      </Section>

      <Section title="Tripwire table" note="Table (Tripwire list), Laminar's columns.">
        <Table.ScrollContainer minWidth={760}>
          <Table highlightOnHover verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Status</Table.Th><Table.Th>Name</Table.Th><Table.Th>Events</Table.Th><Table.Th>Clusters</Table.Th>
                <Table.Th>Activity</Table.Th><Table.Th>Last event</Table.Th><Table.Th>Runs</Table.Th><Table.Th>Created</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((t) => (
                <Table.Tr key={t.id}>
                  <Table.Td><StatusBadge status={t.status} /></Table.Td>
                  <Table.Td><Group gap="xs" wrap="nowrap"><Text size="sm" fw={500}>{t.name}</Text><KindBadge kind={t.kind} size="xs" /></Group></Table.Td>
                  <Table.Td>{t.events}</Table.Td>
                  <Table.Td>{t.clusters}</Table.Td>
                  <Table.Td><ActivitySparkline data={t.activity} /></Table.Td>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>{t.lastEventAt ? formatTime(t.lastEventAt) : '—'}</Table.Td>
                  <Table.Td>{t.runs}</Table.Td>
                  <Table.Td>{formatDay(t.createdAt)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Section>

      <Section title="Events over time" note="EventsOverTimeChart (Tripwire detail). Friction Detector.">
        <EventsOverTimeChart data={eventsPerDay(friction)} />
      </Section>

      <Section title="Cluster tree" note="Paper rows (Tripwire detail). Friction Detector.">
        <Stack gap="xs" maw={760}>
          {topClusters(friction).map((top) => (
            <Paper key={top.id} withBorder p="sm">
              <Group justify="space-between" mb="xs">
                <Text fw={600} size="sm">{top.name}</Text>
                <Text size="xs" c="dimmed">{eventsInCluster(top.id).length} events</Text>
              </Group>
              <Stack gap={6} pl="md" style={{ borderLeft: '2px solid var(--mantine-color-dark-4)' }}>
                {subClusters(top.id).map((c) => (
                  <div key={c.id}>
                    <Group justify="space-between" wrap="nowrap" align="flex-start">
                      <Text size="sm" fw={500}>{c.name}</Text>
                      <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>{eventsInCluster(c.id).length} events</Text>
                    </Group>
                    <Text size="xs" c="dimmed">{c.summary}</Text>
                  </div>
                ))}
              </Stack>
            </Paper>
          ))}
        </Stack>
      </Section>

      <Section title="Events table" note="Table + SeverityBadge + LaminarLink (Tripwire detail).">
        <Table.ScrollContainer minWidth={760}>
          <Table striped highlightOnHover verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Time</Table.Th><Table.Th w={110}>Severity</Table.Th><Table.Th>What the visitor said</Table.Th>
                <Table.Th>Cluster</Table.Th><Table.Th>App version</Table.Th><Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {events.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatTime(e.time)}</Table.Td>
                  <Table.Td><SeverityBadge severity={e.severity} /></Table.Td>
                  <Table.Td>{e.excerpt.visitor}</Table.Td>
                  <Table.Td>{getCluster(e.clusterId).name}</Table.Td>
                  <Table.Td><Code>{e.appVersion}</Code></Table.Td>
                  <Table.Td><LaminarLink traceId={e.traceId} /></Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Section>

      <Section title="Form fields" note="Inputs (New tripwire).">
        <Card withBorder padding="lg" maw={640}>
          <Stack>
            <TextInput id="ss-name" label="Name" defaultValue="Made-up park facts" />
            <Textarea id="ss-instruction" label="What should this tripwire look for?" autosize minRows={2}
              defaultValue={getTripwire('made-up-park-facts').instruction} />
            <Group grow>
              <TextInput id="ss-filter" label="Only read traces where" defaultValue="metadata.app_version starts with v1.4" />
              <TextInput id="ss-sample" label="Sample rate" defaultValue="100%" />
            </Group>
            <Switch id="ss-backfill" label="Run on past traces (last 14 days)" defaultChecked />
            <Group justify="flex-end">
              <Button variant="subtle" color="gray">Cancel</Button>
              <Button>Create tripwire</Button>
            </Group>
          </Stack>
        </Card>
      </Section>

      <Section title="Notifications" note="Paper rows + NotificationTypeBadge (Notifications).">
        <Stack gap="xs" maw={640}>
          {notifications.slice(0, 3).map((n) => (
            <Paper key={n.id} withBorder p="sm">
              <Group gap="xs">
                <NotificationTypeBadge type={n.type} />
                <Text size="sm" fw={n.read ? 400 : 600}>{n.text}</Text>
              </Group>
              <Text size="xs" c="dimmed" mt={4}>{formatTime(n.time)}</Text>
            </Paper>
          ))}
        </Stack>
      </Section>
    </Stack>
  );
}
