import { AppShell, Burger, Group, Title, NavLink as MantineNavLink } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { NavLink, Outlet } from 'react-router-dom';
import { homeUrl, tripwireListUrl } from './links';

const NAV = [
  { to: homeUrl(), label: 'Home', end: true },
  { to: tripwireListUrl(), label: 'Signals' },
];

// The frame around every page: header (product name) and sidebar.
// Notifications (bell, page, sidebar link) come back with alerts: FEATURES.notifications, HIDDEN_CONTROLS.md.
export default function AppLayout() {
  const [opened, { toggle, close }] = useDisclosure();

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 220, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={4}>Tripwire</Title>
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
