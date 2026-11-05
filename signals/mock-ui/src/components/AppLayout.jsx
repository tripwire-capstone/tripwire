import { AppShell, Burger, Group, Title, NavLink as MantineNavLink, Indicator, ActionIcon, Badge } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useStoryData, StoryDayPicker } from './StoryClock';
import { homeUrl, tripwireListUrl, notificationsUrl } from './links';

const NAV = [
  { to: homeUrl(), label: 'Home', end: true },
  { to: tripwireListUrl(), label: 'Signals' },
  { to: notificationsUrl(), label: 'Notifications' },
];

function BellIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  );
}

// The frame around every page: header (product name + notification bell) and sidebar.
export default function AppLayout() {
  const [opened, { toggle, close }] = useDisclosure();
  const navigate = useNavigate();
  const unread = useStoryData().unreadCount();

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 220, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={4}>Tripwire</Title>
            <Badge variant="light" color="gray">Mock</Badge>
          </Group>
          <Group gap="md" wrap="nowrap">
          <StoryDayPicker />
          <Indicator label={unread} size={16} disabled={!unread} color="red">
            <ActionIcon variant="subtle" color="gray" size="lg" aria-label="Notifications" onClick={() => navigate(notificationsUrl())}>
              <BellIcon />
            </ActionIcon>
          </Indicator>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} onClick={close} style={{ textDecoration: 'none', color: 'inherit' }}>
            {({ isActive }) => <MantineNavLink component="span" label={item.label} active={isActive} />}
          </NavLink>
        ))}
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
