import { useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Anchor,
  Button,
  Checkbox,
  Group,
  Modal,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { Link, useNavigate } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import { StatusBadge } from "../components/Badges";
import { ActivitySparkline } from "../components/Charts";
import { FEATURES, useSignals } from "../api";
import { formatDay, formatTime, plural } from "../components/format";
import { newTripwireUrl, tripwireUrl } from "../components/links";

// Deletion only hides rows until a refresh: there is no delete endpoint yet (FEATURES.deleteSignals).
let deletedTripwireIds = new Set();

// From local midnight `days - 1` days ago until now, in the pages' time format (UTC, no zone).
function lastDays(days) {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - days + 1);
  const pageTime = (d) => d.toISOString().slice(0, 19);
  return { start: pageTime(start), end: pageTime(now) };
}

function Icon({ name }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {name === "search" && (
        <>
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m16 16 4 4" />
        </>
      )}
      {name === "trash" && (
        <path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" />
      )}
      {name === "close" && <path d="m6 6 12 12M18 6 6 18" />}
      {name === "plus" && <path d="M12 5v14M5 12h14" />}
    </svg>
  );
}

function Count({ children }) {
  return (
    <Text
      component="span"
      size="sm"
      fw={500}
      c="var(--mantine-color-text)"
      style={{ fontVariantNumeric: "tabular-nums" }}
    >
      {children}
    </Text>
  );
}

export default function TripwireList() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [days, setDays] = useState("7");
  // Computed once per choice of range, so the list doesn't reload on every render.
  const range = useMemo(() => lastDays(Number(days)), [days]);
  const { data, error, loading, reload } = useSignals(range);
  const [deleted, setDeleted] = useState(deletedTripwireIds);
  const [selected, setSelected] = useState(new Set());
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState("");
  const available = (data ?? []).filter((t) => !deleted.has(t.id));
  const rows = available.filter((t) =>
    t.name.toLowerCase().includes(search.trim().toLowerCase()),
  );
  const selectedIds = available
    .filter((t) => selected.has(t.id))
    .map((t) => t.id);
  const allSelected = rows.length > 0 && rows.every((t) => selected.has(t.id));
  const someSelected = rows.some((t) => selected.has(t.id));

  function toggle(id, checked) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function deleteSelected() {
    const next = new Set([...deleted, ...selectedIds]);
    deletedTripwireIds = next;
    setDeleted(next);
    setSelected(new Set());
    setConfirming(false);
    setNotice(
      `${plural(selectedIds.length, "signal")} hidden until you refresh.`,
    );
  }

  return (
    <Stack gap="md">
      <PageHeader
        title="Signals"
        description="View your signals and their results."
        actions={
          <Button
            component={Link}
            to={newTripwireUrl()}
            leftSection={<Icon name="plus" />}
          >
            New signal
          </Button>
        }
      />
      <Group justify="space-between">
        <TextInput
          aria-label="Search signals by name"
          placeholder="Search by name…"
          leftSection={<Icon name="search" />}
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
          w={{ base: "100%", sm: 300 }}
        />
        <Select
          aria-label="Time range"
          value={days}
          onChange={(value) => value && setDays(value)}
          allowDeselect={false}
          w={{ base: "100%", sm: 160 }}
          data={[
            { value: "7", label: "Last 7 days" },
            { value: "3", label: "Last 3 days" },
            { value: "1", label: "Today" },
          ]}
        />
      </Group>
      {error && (
        <Alert color="red" title="Couldn't load signals">
          <Group justify="space-between">
            <Text size="sm">{error.message}</Text>
            <Button variant="light" color="gray" onClick={reload}>
              Try again
            </Button>
          </Group>
        </Alert>
      )}
      <Group justify="space-between">
        <Text size="xs" c="dimmed" aria-live="polite">
          {loading ? "Loading…" : plural(rows.length, "signal")}
        </Text>
        <Text size="xs" c="dimmed">
          {formatDay(range.start)} – {formatDay(range.end)}
        </Text>
      </Group>
      <Table.ScrollContainer minWidth={800}>
        <Table
          highlightOnHover
          verticalSpacing="md"
          horizontalSpacing="sm"
          style={{ color: "var(--mantine-color-text)" }}
        >
          <Table.Thead>
            <Table.Tr>
              {FEATURES.deleteSignals && (
                <Table.Th w={42}>
                  <Checkbox
                    radius="xs"
                    aria-label="Select all visible signals"
                    checked={allSelected}
                    indeterminate={someSelected && !allSelected}
                    disabled={!rows.length}
                    onChange={(event) => {
                      const checked = event.currentTarget.checked;
                      setSelected((previous) => {
                        const next = new Set(previous);
                        rows.forEach((t) => {
                          if (checked) next.add(t.id);
                          else next.delete(t.id);
                        });
                        return next;
                      });
                    }}
                  />
                </Table.Th>
              )}
              <Table.Th>Status</Table.Th>
              <Table.Th>Name</Table.Th>
              <Table.Th ta="right">Events</Table.Th>
              <Table.Th ta="right">Clusters</Table.Th>
              <Table.Th>Activity</Table.Th>
              <Table.Th>Last event</Table.Th>
              <Table.Th ta="right">Runs</Table.Th>
              <Table.Th>Created</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((t) => (
              <Table.Tr
                key={t.id}
                onClick={() => navigate(tripwireUrl(t.id))}
                bg={
                  selected.has(t.id)
                    ? "var(--mantine-color-teal-light)"
                    : undefined
                }
                style={{ cursor: "pointer" }}
              >
                {FEATURES.deleteSignals && (
                  <Table.Td onClick={(event) => event.stopPropagation()}>
                    <Checkbox
                      radius="xs"
                      aria-label={`Select ${t.name}`}
                      checked={selected.has(t.id)}
                      onChange={(event) =>
                        toggle(t.id, event.currentTarget.checked)
                      }
                    />
                  </Table.Td>
                )}
                <Table.Td>
                  <StatusBadge status={t.status} />
                </Table.Td>
                <Table.Td miw={160}>
                  <Anchor
                    component={Link}
                    to={tripwireUrl(t.id)}
                    c="var(--mantine-color-text)"
                    size="sm"
                    fw={500}
                    onClick={(event) => event.stopPropagation()}
                  >
                    {t.name}
                  </Anchor>
                </Table.Td>
                <Table.Td ta="right">
                  <Count>{t.events}</Count>
                </Table.Td>
                                  <Table.Td ta="right">
                    <Count>{t.clusters}</Count>
                  </Table.Td>
                                  <Table.Td>
                    <ActivitySparkline data={t.activity} w={80} />
                  </Table.Td>
                <Table.Td>
                  <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>
                    {t.lastEventAt ? formatTime(t.lastEventAt) : "—"}
                  </Text>
                </Table.Td>
                <Table.Td ta="right">
                  <Count>{t.runs}</Count>
                </Table.Td>
                <Table.Td>
                  <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>
                    {formatDay(t.createdAt)}
                  </Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {!loading && !error && !rows.length && (
        <Stack align="center" py="xl" gap="sm">
          <Text fw={500}>
            {search.trim() ? "No matching signals" : "No signals yet"}
          </Text>
          <Text size="sm" c="dimmed">
            {search.trim()
              ? "Try a different name."
              : "Create a signal to get started."}
          </Text>
          {search.trim() && (
            <Button variant="subtle" color="gray" onClick={() => setSearch("")}>
              Clear search
            </Button>
          )}
        </Stack>
      )}
      {selectedIds.length > 0 && (
        <Paper withBorder radius="md" p="sm" mx="auto" shadow="md">
          <Group gap="md">
            <Text size="sm" aria-live="polite">
              {plural(selectedIds.length, "row")} selected
            </Text>
            <Button
              variant="light"
              color="gray"
              leftSection={<Icon name="trash" />}
              onClick={() => setConfirming(true)}
            >
              Delete
            </Button>
            <ActionIcon
              aria-label="Clear selection"
              variant="subtle"
              color="gray"
              onClick={() => setSelected(new Set())}
            >
              <Icon name="close" />
            </ActionIcon>
          </Group>
        </Paper>
      )}
      {notice && (
        <Text role="status" size="sm" c="dimmed">
          {notice}
        </Text>
      )}
      <Modal
        opened={confirming}
        onClose={() => setConfirming(false)}
        title="Delete signals"
        centered
      >
        <Text size="sm">
          Are you sure you want to delete{" "}
          {plural(selectedIds.length, "signal")}?
        </Text>
        <Group justify="flex-end" mt="lg">
          <Button
            variant="light"
            color="gray"
            onClick={() => setConfirming(false)}
          >
            Cancel
          </Button>
          <Button disabled={!selectedIds.length} onClick={deleteSelected}>
            Delete
          </Button>
        </Group>
      </Modal>
    </Stack>
  );
}
