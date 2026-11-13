import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Popover, UnstyledButton, Group, Badge, Text, TextInput, Stack, Button, Divider } from '@mantine/core';
import { DatePicker } from '@mantine/dates';
import dayjs from 'dayjs';
import { PRESETS, rangeFromParams, withRange, parseDuration } from './timeRange';
import { formatTime, toDate, zoneLabel } from './format';

// Time range control, like Laminar's: a chip ("7d") plus the dates, opening a menu with
// a typed duration ("4 hours, 8 days"), presets, and an absolute date range.
// It reads and writes the page address (?pastHours= or ?startDate=&endDate=).
// defaultHours: the range shown when the address has none (must match the page's own rangeFromParams call).
// now: the page's "now" (UTC, no zone), read once and again on Refresh; defaults to the real clock.
export default function TimeRangePicker({ defaultHours, now }) {
  const [params, setParams] = useSearchParams();
  const asOf = now ?? new Date().toISOString().slice(0, 19);
  const range = rangeFromParams(params, asOf, defaultHours);
  const [opened, setOpened] = useState(false);
  const [view, setView] = useState('menu'); // 'menu' | 'calendar'
  const [text, setText] = useState('');
  const [error, setError] = useState(null);
  const [dates, setDates] = useState([null, null]);

  const close = () => { setOpened(false); setView('menu'); setText(''); setError(null); };
  const applyHours = (hours) => { setParams(withRange(params, { hours, absolute: false })); close(); };
  const applyText = () => {
    const hours = parseDuration(text);
    if (!hours) { setError('Try something like "4 hours" or "8 days"'); return; }
    applyHours(hours);
  };
  const applyDates = () => {
    const [from, to] = dates;
    setParams(withRange(params, {
      absolute: true,
      // The picked days are local days; the address keeps UTC, like every other time in the pages.
      start: dayjs(from).startOf('day').toDate().toISOString().slice(0, 19),
      end: dayjs(to ?? from).endOf('day').toDate().toISOString().slice(0, 19),
    }));
    close();
  };

  return (
    <Popover opened={opened} onChange={(o) => (o ? setOpened(true) : close())} position="bottom-start" width={280} shadow="md">
      <Popover.Target>
        <UnstyledButton onClick={() => (opened ? close() : setOpened(true))}
          style={{ border: '1px solid var(--mantine-color-dark-4)', borderRadius: 'var(--mantine-radius-md)', padding: '4px 10px' }}>
          <Group gap="xs" wrap="nowrap">
            <Badge variant="filled" color="dark.4" radius="sm">{range.label}</Badge>
            <Text size="sm" c="dimmed" style={{ whiteSpace: 'nowrap' }}>{formatTime(range.start)} – {formatTime(range.end)} {zoneLabel(range.end)}</Text>
          </Group>
        </UnstyledButton>
      </Popover.Target>
      <Popover.Dropdown p="xs">
        {view === 'menu' ? (
          <Stack gap={2}>
            <TextInput id="time-range-text" size="xs" placeholder="4 hours, 8 days…" value={text} error={error} data-autofocus
              onChange={(e) => { setText(e.currentTarget.value); setError(null); }}
              onKeyDown={(e) => { if (e.key === 'Enter') applyText(); }} mb={4} />
            {PRESETS.map((p) => (
              <MenuRow key={p.label} active={!range.absolute && range.hours === p.hours} onClick={() => applyHours(p.hours)}>
                {p.label}
              </MenuRow>
            ))}
            <Divider my={4} />
            <MenuRow onClick={() => setView('calendar')}>
              <Group justify="space-between"><span>Absolute date</span><span aria-hidden="true">›</span></Group>
            </MenuRow>
          </Stack>
        ) : (
          <Stack gap="xs" align="center">
            <DatePicker type="range" value={dates} onChange={setDates} maxDate={toDate(asOf)} />
            <Group justify="space-between" w="100%">
              <Button size="xs" variant="subtle" color="gray" onClick={() => setView('menu')}>Back</Button>
              <Button size="xs" disabled={!dates[0]} onClick={applyDates}>Apply</Button>
            </Group>
          </Stack>
        )}
      </Popover.Dropdown>
    </Popover>
  );
}

function MenuRow({ active, onClick, children }) {
  return (
    <UnstyledButton onClick={onClick} px="xs" py={6}
      style={{ borderRadius: 'var(--mantine-radius-sm)', fontSize: 'var(--mantine-font-size-sm)',
        background: active ? 'var(--mantine-color-dark-5)' : undefined }}>
      {children}
    </UnstyledButton>
  );
}
