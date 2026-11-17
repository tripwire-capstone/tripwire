import { Alert, Anchor, AppShell, Burger, Group, Title, NavLink as MantineNavLink } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconBroadcast, IconHome, IconSettings, IconTerminal2 } from '@tabler/icons-react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useSettings } from '../api';
import { homeUrl, settingsUrl, tripwireListUrl } from './links';
import { devUrl } from './links'; // dev view

const ICON_SIZE = 18;
const ICON_STROKE = 1.6;

const NAV = [
  { to: homeUrl(), label: 'Home', icon: IconHome, end: true },
  { to: tripwireListUrl(), label: 'Signals', icon: IconBroadcast },
];

function SidebarLink({ to, label, icon: Icon, end, dimmed, onClick, style }) {
  return (
    <NavLink to={to} end={end} onClick={onClick} style={{ textDecoration: 'none', color: 'inherit', ...style }}>
      {({ isActive }) => (
        <MantineNavLink component="span" label={label} active={isActive} c={dimmed ? 'dimmed' : undefined}
          leftSection={<Icon size={ICON_SIZE} stroke={ICON_STROKE} />} />
      )}
    </NavLink>
  );
}

// Shown on every page while the Laminar or OpenAI key is missing (not on Settings itself, which says
// the same thing in place). Nothing shows while settings load or when the API can't be reached:
// pages show their own "can't reach the API" message.
function NotConnectedBanner({ settings }) {
  const { pathname } = useLocation();
  if (!settings || settings.connected || pathname === settingsUrl()) return null;
  return (
    <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={ICON_SIZE} />} mb="lg"
      title="Not connected yet">
      Add your Laminar and OpenAI API keys in{' '}
      <Anchor component={Link} to={settingsUrl()} inherit>Settings</Anchor>
      {' '}so signals can start reading traces.
    </Alert>
  );
}

// The frame around every page: header (product name), sidebar, and the "not connected yet" banner.
// Notifications (bell, page, sidebar link) come back with alerts: FEATURES.notifications, HIDDEN_CONTROLS.md.
export default function AppLayout() {
  const [opened, { toggle, close }] = useDisclosure();
  // Loaded once here and shared with the Settings page, so saving a key updates the banner at once.
  const settings = useSettings();

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
        {NAV.map((item) => <SidebarLink key={item.to} {...item} onClick={close} />)}
        {/* Settings sits at the bottom, as in Laminar's sidebar */}
        <SidebarLink to={settingsUrl()} label="Settings" icon={IconSettings} onClick={close} style={{ marginTop: 'auto' }} />
        {/* dev view: a dim link at the bottom of the sidebar, for the team. Remove with pages/Dev.jsx. */}
        <SidebarLink to={devUrl()} label="Developer" icon={IconTerminal2} dimmed onClick={close} />
      </AppShell.Navbar>

      <AppShell.Main>
        <NotConnectedBanner settings={settings.data} />
        <Outlet context={{ settings }} />
      </AppShell.Main>
    </AppShell>
  );
}
