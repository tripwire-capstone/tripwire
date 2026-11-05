import { createContext, useContext, useMemo, useState } from 'react';
import { SegmentedControl, Tooltip, Group, Text } from '@mantine/core';
import { withNotificationData, readNotificationIds, saveNotificationIds } from './notificationData';
import { STORY_DAYS, DEFAULT_STORY_DAY, makeStoryData } from '../mock/data';

// The story clock: which day of the Olympic story the mock is showing.
// It's a demo control, not a product feature. Every page reads data through useStoryData(),
// so changing the day in the header changes every screen at once.

// Hidden while we build the pages: the mock always shows "Today".
// Set to true to show the day picker in the header (for rehearsing the story).
export const SHOW_STORY_DAY_PICKER = false;

const StoryClockContext = createContext(null);

function readSavedDay() {
  try { return sessionStorage.getItem('storyDay') || DEFAULT_STORY_DAY; } catch { return DEFAULT_STORY_DAY; }
}

export function StoryClockProvider({ children }) {
  const [dayId, setDayIdState] = useState(readSavedDay);
  const setDayId = (id) => {
    setDayIdState(id);
    try { sessionStorage.setItem('storyDay', id); } catch { /* storage unavailable: keep it in memory only */ }
  };
  // With the picker hidden, ignore any day saved earlier, so nobody gets stuck on a day they can't see.
  const activeId = SHOW_STORY_DAY_PICKER ? dayId : DEFAULT_STORY_DAY;
  const day = STORY_DAYS.find((d) => d.id === activeId) ?? STORY_DAYS.find((d) => d.id === DEFAULT_STORY_DAY);
  const base = useMemo(() => makeStoryData(day.asOf), [day.asOf]);
  const [readIds, setReadIds] = useState(readNotificationIds);
  const data = useMemo(() => {
    const markIds = (ids) => setReadIds((previous) => {
      const next = [...new Set([...previous, ...ids])];
      saveNotificationIds(next);
      return next;
    });
    return withNotificationData(base, readIds, {
      markNotificationRead: (id) => { if (base.notifications.some((n) => n.id === id)) markIds([id]); },
      markAllNotificationsRead: () => markIds(base.notifications.map((n) => n.id)),
    });
  }, [base, readIds]);
  const value = useMemo(() => ({ day, setDayId, data }), [day, data]);
  return <StoryClockContext.Provider value={value}>{children}</StoryClockContext.Provider>;
}

// All the mock data as of the chosen story day. Use this in every page and component.
export function useStoryData() {
  const ctx = useContext(StoryClockContext);
  if (!ctx) throw new Error('useStoryData() must be used inside <StoryClockProvider>');
  return ctx.data;
}

// The chosen day itself ({ id, label, asOf, note }), e.g. to show "as of Day 3".
export function useStoryDay() {
  return useContext(StoryClockContext).day;
}

// The day picker shown in the header.
export function StoryDayPicker() {
  const { day, setDayId } = useContext(StoryClockContext);
  if (!SHOW_STORY_DAY_PICKER) return null;
  return (
    <Group gap="xs" wrap="nowrap">
      <Text size="xs" c="dimmed" visibleFrom="md">Story day</Text>
      <Tooltip label={day.note} withArrow>
        <SegmentedControl size="xs" value={day.id} onChange={setDayId}
          data={STORY_DAYS.map((d) => ({ value: d.id, label: d.label }))} />
      </Tooltip>
    </Group>
  );
}
