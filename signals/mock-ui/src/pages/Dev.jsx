import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Anchor, Button, CopyButton, Group, Paper, Stack, Switch, Table, Text, Title } from '@mantine/core';
import { useDevStatus } from '../api';
import { SeverityBadge } from '../components/Badges';
import { formatRelative, formatTime, plural, zoneLabel } from '../components/format';
import { tripwireUrl } from '../components/links';

// Developer view: is data moving through Tripwire, and if not, where is it stuck and why?
// For the team, not end users. Reads GET /api/dev/status (backend tripwire/api/dev.py, which also says how
// to add a section and how to remove the whole view). Every number is counted in the database.

const STAGE_LABELS = {
  traces: 'Traces (poller)',
  runs: 'Signal runs (runner)',
  events: 'Signal events (runner)',
  summaries: 'Summaries (clusterer)',
  embeddings: 'Embeddings (clusterer)',
  clusters: 'Clusters (clusterer)',
};
const stageLabel = (stage) => STAGE_LABELS[stage] ?? stage;

// States worth a color when they're above zero. Names are the tables' own (status, error_kind, active).
const WARN_STATES = new Set(['retrying', 'lease_expired']);
const isFailure = (state) => state === 'failed' || state.startsWith('failed:');
const label = (state) => state.replaceAll('_', ' ');

const AUTO_REFRESH_SECONDS = 10;

export default function Dev() {
  const { data, error, loading, reload } = useDevStatus();
  // For watching a traffic run. Off by default: an open tab shouldn't query the database forever.
  const [auto, setAuto] = useState(false);
  useEffect(() => {
    if (!auto) return undefined;
    const timer = setInterval(reload, AUTO_REFRESH_SECONDS * 1000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto]);

  return (
    <Stack gap="md">
      {/* Left: what you're looking at and how fresh it is. Right: sharing it. */}
      <Group justify="space-between" wrap="wrap">
        <Group gap="sm" wrap="wrap">
          <Title order={3}>Developer view</Title>
          <Button variant="default" size="xs" loading={loading && Boolean(data)} onClick={reload}>↻ Refresh</Button>
          <Switch size="xs" label={`Auto-refresh (${AUTO_REFRESH_SECONDS} s)`} checked={auto} onChange={(e) => setAuto(e.currentTarget.checked)} />
          {data && <Text size="sm" c="dimmed">As of {formatTime(data.asOf)}</Text>}
        </Group>
        {data && (
          <CopyButton value={statusAsText(data)}>
            {({ copied, copy }) => (
              <Button variant="light" size="xs" onClick={copy}>{copied ? 'Copied ✓' : 'Copy as text'}</Button>
            )}
          </CopyButton>
        )}
      </Group>
      <Text size="sm" c="dimmed" maw={760}>
        Is data moving through Tripwire? A step is stuck when something has waited over 2 minutes and nothing got
        through it in the last 10 minutes. Errors say why. “Copy as text” gives a snapshot to paste in Slack.
      </Text>
      {error && <Alert color="red" title="Couldn't load">{error.message}</Alert>}
      {loading && !data && <Text c="dimmed">Loading…</Text>}
      {data && (
        <>
          <Pipeline pipeline={data.pipeline} asOf={data.asOf} errors={data.errors} />
          <LatestDecisions rows={data.latestDecisions} asOf={data.asOf} />
          <LatestSummaries rows={data.latestSummaries} asOf={data.asOf} />
          {/* At the bottom: rarely needed. When it matters, Pipeline points here. */}
          <Errors errors={data.errors} asOf={data.asOf} />
        </>
      )}
    </Stack>
  );
}

function Section({ id, title, note, children }) {
  return (
    <Paper withBorder p="md" id={id}>
      <Text fw={600}>{title}</Text>
      {note && <Text size="xs" c="dimmed" mb="sm">{note}</Text>}
      {children}
    </Paper>
  );
}

const ago = (iso, asOf) => (iso ? formatRelative(iso, asOf) : 'never');

// A time still to come, measured from asOf: 'due now', 'in 40 s', 'in 3 min'.
function until(iso, asOf) {
  const seconds = Math.round((new Date(iso) - new Date(asOf)) / 1000);
  if (seconds <= 0) return 'due now';
  return seconds < 60 ? `in ${seconds} s` : `in ${Math.round(seconds / 60)} min`;
}

// "Last seen" for an error group. Runs that are only still retrying have no recorded error time
// (signal_runs has none), so they say "retrying" with the next try instead of a guessed time.
function lastSeen(e, asOf) {
  const retry = e.pendingOrRunning > 0 && e.nextRetryAt ? `retrying, next try ${until(e.nextRetryAt, asOf)}` : null;
  return { seen: e.lastSeenAt ? formatRelative(e.lastSeenAt, asOf) : null, retry };
}

function Pipeline({ pipeline, asOf, errors }) {
  // Items still pending or running after an error: the case this page exists for (a revoked key).
  const affected = errors.groups.reduce((sum, g) => sum + g.pendingOrRunning, 0);
  return (
    <Section title="Pipeline"
      note={<>
        Last successful poll: <span title={pipeline.lastPolledAt ? formatTime(pipeline.lastPolledAt) : undefined}>{ago(pipeline.lastPolledAt, asOf)}</span>
        {' · '}newest trace ended <span title={pipeline.newestTraceEndedAt ? formatTime(pipeline.newestTraceEndedAt) : undefined}>{ago(pipeline.newestTraceEndedAt, asOf)}</span>
      </>}>
      <Table.ScrollContainer minWidth={720}>
        <Table verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Step</Table.Th>
              <Table.Th>Now</Table.Th>
              <Table.Th>Oldest waiting</Table.Th>
              <Table.Th ta="right">Got through, last 10 min</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pipeline.stages.map((s) => {
              const stalled = s.stuck;
              return (
                <Table.Tr key={s.stage}>
                  <Table.Td><Text size="sm" style={{ whiteSpace: 'nowrap' }}>{stageLabel(s.stage)}</Text></Table.Td>
                  <Table.Td><Counts counts={s.counts} /></Table.Td>
                  <Table.Td>
                    <Text size="sm" c={s.oldestWaitingAt ? undefined : 'dimmed'} title={s.oldestWaitingAt ? formatTime(s.oldestWaitingAt) : undefined}>
                      {s.oldestWaitingAt ? formatRelative(s.oldestWaitingAt, asOf) : '—'}
                    </Text>
                  </Table.Td>
                  <Table.Td ta="right">
                    <Text size="sm" c={stalled ? 'red' : undefined} fw={stalled ? 600 : undefined} style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {s.doneRecently.toLocaleString()}{stalled ? ' (stuck?)' : ''}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Text size="xs" c="dimmed" mt="xs">
        Events in the pool wait for similar events, so the pool has no “oldest waiting”.
      </Text>
      {affected > 0 && (
        <Alert color="yellow" variant="light" mt="sm" p="xs">
          <Text size="sm">
            {plural(affected, 'item')} {affected === 1 ? 'is' : 'are'} pending or running after an error.{' '}
            <Anchor component="button" size="sm" onClick={() => document.getElementById('errors')?.scrollIntoView({ behavior: 'smooth' })}>
              See Errors ↓
            </Anchor>
          </Text>
        </Alert>
      )}
    </Section>
  );
}

// "retrying" is a part of "pending" (pending with last_error set), so it's shown inside it, and only when
// there are any: "pending 400 (retrying 400)". Otherwise "pending 0 · retrying 400" would read as nothing pending.
const withRetrying = (counts) => {
  const retrying = counts.find((c) => c.state === 'retrying')?.count ?? 0;
  return counts.filter((c) => c.state !== 'retrying').map((c) => (c.state === 'pending' ? { ...c, retrying } : c));
};

// "pending 400 (retrying 400) · running 0 · …": zeros dimmed, problems colored.
function Counts({ counts }) {
  const shown = withRetrying(counts);
  return (
    <Group gap={4} wrap="wrap">
      {shown.map((c, i) => {
        const color = c.count === 0 ? 'dimmed' : isFailure(c.state) ? 'red' : WARN_STATES.has(c.state) ? 'yellow' : undefined;
        return (
          <Text key={c.state} size="sm" c={color} style={{ whiteSpace: 'nowrap' }}>
            {label(c.state)} <span style={{ fontVariantNumeric: 'tabular-nums' }}>{c.count.toLocaleString()}</span>
            {c.retrying > 0 && <Text span c="yellow" title="pending with last_error set: will be tried again"> (retrying {c.retrying.toLocaleString()})</Text>}
            {i < shown.length - 1 && <Text span c="dimmed"> · </Text>}
          </Text>
        );
      })}
    </Group>
  );
}

function Errors({ errors, asOf }) {
  return (
    <Section id="errors" title="Errors"
      note="Each row is one last_error message and the items that hit it: how many are still pending or running (they'll be tried again), how many failed for good, and how many were done after it (they recovered). Ones still happening come first, then the newest. Pending or running items are listed however old; failed and done ones for 24 hours.">
      {errors.groups.length === 0
        ? <Empty text="No errors on pending or running items, and none in the last 24 hours." />
        : (
          <Table.ScrollContainer minWidth={720}>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Step</Table.Th>
                  <Table.Th>last_error</Table.Th>
                  <Table.Th ta="right">Pending / running</Table.Th>
                  <Table.Th ta="right">Failed</Table.Th>
                  <Table.Th ta="right" title="status done after this error: it recovered">Done (recovered)</Table.Th>
                  <Table.Th>Last seen</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {errors.groups.map((e) => (
                  <Table.Tr key={`${e.stage}\n${e.message}`}>
                    <Table.Td><Text size="sm" style={{ whiteSpace: 'nowrap' }}>{stageLabel(e.stage)}</Text></Table.Td>
                    <Table.Td maw={520}>
                      <Text size="sm" ff="monospace" lineClamp={3} title={e.message} style={{ wordBreak: 'break-word' }}>{e.message}</Text>
                      <Text size="xs" c="dimmed">{e.signals.length === 1 ? 'Signal' : 'Signals'}: {e.signals.join(', ')}</Text>
                    </Table.Td>
                    <CountCell value={e.pendingOrRunning} color="yellow" />
                    <CountCell value={e.failed} color="red" />
                    <CountCell value={e.done} />
                    <Table.Td>
                      {(() => {
                        const { seen, retry } = lastSeen(e, asOf);
                        return (
                          <>
                            {seen && <Text size="sm" style={{ whiteSpace: 'nowrap' }} title={formatTime(e.lastSeenAt)}>{seen}</Text>}
                            {retry && <Text size={seen ? 'xs' : 'sm'} c="yellow" style={{ whiteSpace: 'nowrap' }} title={formatTime(e.nextRetryAt)}>{retry}</Text>}
                          </>
                        );
                      })()}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      {errors.more && <Text size="xs" c="dimmed" mt="xs">Only the first {errors.groups.length} groups are shown.</Text>}
    </Section>
  );
}

function CountCell({ value, color }) {
  return (
    <Table.Td ta="right">
      <Text size="sm" c={value === 0 ? 'dimmed' : color} style={{ fontVariantNumeric: 'tabular-nums' }}>
        {value.toLocaleString()}
      </Text>
    </Table.Td>
  );
}

// "2 findings", "found nothing", "failed": the judge's outcome in words (the page and the text copy agree).
function outcome(r) {
  if (r.status === 'failed') return 'failed';
  return r.findings.length === 0 ? 'found nothing' : plural(r.findings.length, 'finding');
}

function LatestDecisions({ rows, asOf }) {
  return (
    <Section title="Latest judge decisions" note="The newest finished signal runs, including the ones that found nothing, with the judge's reasoning. Hover a reason for the full text.">
      {rows.length === 0
        ? <Empty text="No finished runs yet." />
        : (
          <Table.ScrollContainer minWidth={720}>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Finished</Table.Th>
                  <Table.Th>Signal</Table.Th>
                  <Table.Th>Outcome</Table.Th>
                  <Table.Th>Analysis</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((r) => {
                  // A failed run has no analysis; its error is shown in the same cell, labeled with its column.
                  const why = r.status === 'failed' ? (r.error && `last_error: ${r.error}`) : r.analysis;
                  return (
                    <Table.Tr key={r.runId}>
                      <Table.Td><Text size="sm" style={{ whiteSpace: 'nowrap' }} title={formatTime(r.finishedAt)}>{formatRelative(r.finishedAt, asOf)}</Text></Table.Td>
                      <Table.Td>
                        <Anchor component={Link} to={tripwireUrl(r.signalId, { tab: 'runs' })} size="sm">{r.signalName}</Anchor>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" c={r.status === 'failed' ? 'red' : r.findings.length ? undefined : 'dimmed'} style={{ whiteSpace: 'nowrap' }}>{outcome(r)}</Text>
                        {r.findings.length > 0 && (
                          <Group gap={4} mt={2} wrap="nowrap">{r.findings.map((severity, i) => <SeverityBadge key={i} severity={severity} />)}</Group>
                        )}
                        {r.droppedFindings > 0 && (
                          <Text size="xs" c="yellow" title="Findings removed because their evidence didn't check out">{r.droppedFindings} dropped</Text>
                        )}
                      </Table.Td>
                      <Table.Td maw={600}>
                        <Text size="sm" lineClamp={3} title={why ?? ''} ff={r.status === 'failed' ? 'monospace' : undefined}
                          c={why ? undefined : 'dimmed'} style={{ wordBreak: 'break-word' }}>{why ?? 'No reasoning recorded.'}</Text>
                        <Text size="xs" c="dimmed">run {r.runId}</Text>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
    </Section>
  );
}

const LANDED = { pool: 'In the pool', not_embedded: 'Not embedded yet' };

function LatestSummaries({ rows, asOf }) {
  return (
    <Section title="Latest summaries" note="The newest finished summaries and where each event landed. Do they make sense, and do similar ones share a cluster?">
      {rows.length === 0
        ? <Empty text="No summaries yet." />
        : (
          <Table.ScrollContainer minWidth={720}>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Finished</Table.Th>
                  <Table.Th>Signal</Table.Th>
                  <Table.Th>Summary</Table.Th>
                  <Table.Th>Landed in</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((r) => (
                  <Table.Tr key={r.eventId}>
                    <Table.Td><Text size="sm" style={{ whiteSpace: 'nowrap' }} title={formatTime(r.finishedAt)}>{formatRelative(r.finishedAt, asOf)}</Text></Table.Td>
                    <Table.Td>
                      <Anchor component={Link} to={tripwireUrl(r.signalId)} size="sm">{r.signalName}</Anchor>
                    </Table.Td>
                    <Table.Td maw={520}>
                      <Text size="sm">{r.summary}</Text>
                      <Text size="xs" c="dimmed">
                        {r.cause ? `Cause: ${r.cause} · ` : ''}event {r.eventId} · prompt v{r.promptVersion}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      {r.landed === 'cluster'
                        ? <Anchor component={Link} to={tripwireUrl(r.signalId, { clusterId: r.clusterId })} size="sm">{r.clusterName ?? `Unnamed cluster ${r.clusterId}`}</Anchor>
                        : <Text size="sm" c="dimmed">{LANDED[r.landed] ?? r.landed}</Text>}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
    </Section>
  );
}

function Empty({ text }) {
  return <Text size="sm" c="dimmed" py="md">{text}</Text>;
}

// The page as plain text, for pasting into Slack or a PR. Zero counts are left out to keep it short.
// Adding a section? Add its lines here too.
function statusAsText(data) {
  const { asOf, pipeline, errors, latestDecisions, latestSummaries } = data;
  const lines = [
    `Tripwire /dev, ${formatTime(asOf)} ${zoneLabel(asOf)}`,
    `Last successful poll ${ago(pipeline.lastPolledAt, asOf)}; newest trace ended ${ago(pipeline.newestTraceEndedAt, asOf)}`,
    '',
    'Pipeline:',
  ];
  for (const s of pipeline.stages) {
    const counts = withRetrying(s.counts).filter((c) => c.count > 0)
      .map((c) => `${label(c.state)} ${c.count}${c.retrying ? ` (retrying ${c.retrying})` : ''}`).join(', ') || 'nothing';
    const oldest = s.oldestWaitingAt ? `; oldest waiting ${formatRelative(s.oldestWaitingAt, asOf)}` : '';
    lines.push(`- ${stageLabel(s.stage)}: ${counts}${oldest}; got through in 10 min: ${s.doneRecently}${s.stuck ? ' (STUCK?)' : ''}`);
  }
  lines.push('', 'Errors:');
  if (errors.groups.length === 0) lines.push('- none');
  for (const e of errors.groups.slice(0, 10)) {
    const message = e.message.length > 200 ? `${e.message.slice(0, 200)}…` : e.message;
    const { seen, retry } = lastSeen(e, asOf);
    const when = [seen && `last seen ${seen}`, retry].filter(Boolean).join(', ');
    lines.push(`- ${stageLabel(e.stage)} [${e.signals.join(', ')}]: ${e.pendingOrRunning} pending/running, ${e.failed} failed, ${e.done} done (${when}): ${message}`);
  }
  if (errors.groups.length > 10 || errors.more) lines.push('- (more on /dev)');
  lines.push('', 'Latest judge decisions:');
  if (latestDecisions.length === 0) lines.push('- none');
  for (const r of latestDecisions.slice(0, 5)) {
    const why = (r.status === 'failed' ? `last_error: ${r.error ?? ''}` : r.analysis) ?? '';
    const short = why.length > 160 ? `${why.slice(0, 160)}…` : why;
    const severities = r.findings.length ? ` (${r.findings.join(', ')})` : '';
    lines.push(`- ${r.signalName}, run ${r.runId}: ${outcome(r)}${severities}${r.droppedFindings ? `, ${r.droppedFindings} dropped` : ''}: ${short}`);
  }
  lines.push('', 'Latest summaries:');
  if (latestSummaries.length === 0) lines.push('- none');
  for (const r of latestSummaries.slice(0, 5)) {
    const landed = r.landed === 'cluster' ? (r.clusterName ?? `unnamed cluster ${r.clusterId}`) : (LANDED[r.landed] ?? r.landed).toLowerCase();
    lines.push(`- ${r.signalName}: "${r.summary}" -> ${landed}`);
  }
  return lines.join('\n');
}
