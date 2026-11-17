import { useState } from 'react';
import { useOutletContext, useSearchParams } from 'react-router-dom';
import {
  ActionIcon, Alert, Anchor, Badge, Button, Group, Modal, PasswordInput, Stack, Table, Text, Title, Tooltip, UnstyledButton,
} from '@mantine/core';
import { IconAlertTriangle, IconBell, IconBrandOpenai, IconExternalLink, IconPlugConnected, IconPlus, IconTrash } from '@tabler/icons-react';
import { FEATURES, clearKey, saveKey } from '../api';
import PageHeader from '../components/PageHeader';

// Settings, modeled on Laminar's: a section menu on the left ("Project settings"), and for each provider
// a title, one sentence, an "API key" button and a one-row table (project, key hint, where the key comes
// from, remove). Keys are checked by the API with Laminar or OpenAI before they're saved, and only hints
// ever come back. The layout (AppLayout) loads the settings and shares them here, so the "not connected
// yet" banner updates as soon as a key is saved.

const ICON_SIZE = 18;
const ICON_STROKE = 1.6;

const SECTIONS = [
  {
    id: 'laminar', label: 'Laminar', icon: IconPlugConnected, shown: true,
    title: 'Laminar API key',
    description: 'Signals read traces from the Laminar project this key belongs to. Create a project API key in Laminar '
      + '(Settings → Project API Keys) and paste it here.',
  },
  {
    id: 'openai', label: 'OpenAI', icon: IconBrandOpenai, shown: true,
    title: 'OpenAI API key',
    description: 'Used to judge traces, and to summarize and group events into clusters.',
  },
  // Slack webhook: comes back with alerts (FEATURES.notifications, HIDDEN_CONTROLS.md).
  { id: 'notifications', label: 'Notifications', icon: IconBell, shown: FEATURES.notifications, title: 'Notifications' },
].filter((section) => section.shown);

const SOURCE_BADGE = {
  saved: { label: 'Saved', color: 'teal', tip: 'Saved on this page.' },
  // Only when running locally: on AWS there's no fallback key, so a key is saved here or not set.
  environment: {
    label: 'From .env', color: 'gray',
    tip: 'Local fallback from .env. A key saved here takes its place.',
  },
};

export default function Settings() {
  const { settings } = useOutletContext();
  const [params, setParams] = useSearchParams();
  const section = SECTIONS.find((x) => x.id === params.get('section')) ?? SECTIONS[0];
  const open = (id) => setParams(id === SECTIONS[0].id ? {} : { section: id });

  return (
    <>
      <PageHeader title="Settings" />
      <Group align="flex-start" gap="xl" wrap="nowrap">
        <Stack gap={2} w={200} style={{ flexShrink: 0 }}>
          <Text size="sm" c="dimmed" px="sm" mb={4}>Project settings</Text>
          {SECTIONS.map((x) => (
            <UnstyledButton key={x.id} onClick={() => open(x.id)} px="sm" py={6}
              style={{ borderRadius: 'var(--mantine-radius-md)', background: x.id === section.id ? 'var(--mantine-color-dark-5)' : undefined }}>
              <Group gap="sm" wrap="nowrap">
                <x.icon size={ICON_SIZE} stroke={ICON_STROKE} />
                <Text size="sm" fw={x.id === section.id ? 600 : 400}>{x.label}</Text>
              </Group>
            </UnstyledButton>
          ))}
        </Stack>
        <Stack gap="md" style={{ flex: 1, minWidth: 0 }} maw={900}>
          <div>
            <Title order={3}>{section.title}</Title>
            {section.description && <Text c="dimmed" mt={4}>{section.description}</Text>}
          </div>
          {settings.error && <Alert color="red" title="Couldn't load settings">{settings.error.message}</Alert>}
          {!settings.data && settings.loading && <Text c="dimmed">Loading…</Text>}
          {settings.data && section.id !== 'notifications' && (
            <KeySection kind={section.id} title={section.title} info={settings.data[section.id]} onChanged={settings.reload} />
          )}
          {section.id === 'notifications' && <Text c="dimmed">Placeholder</Text>}
        </Stack>
      </Group>
    </>
  );
}

// One provider's key: the "API key" button, the table row, and the dialogs to set or remove it.
function KeySection({ kind, title, info, onChanged }) {
  const [setting, setSetting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const isLaminar = kind === 'laminar';

  return (
    <Stack gap="md">
      {info.unreadable && (
        <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={ICON_SIZE} />} title="The saved key can't be read">
          The server&apos;s encryption key changed since it was saved. Set the key again.
          {info.source === 'environment' && ' Until then, the key from .env is used.'}
        </Alert>
      )}
      <Group>
        <Button variant="default" size="xs" leftSection={<IconPlus size={14} />} onClick={() => setSetting(true)}>
          {info.source === 'saved' ? 'Replace API key' : 'API key'}
        </Button>
      </Group>
      <Table withTableBorder verticalSpacing="sm" horizontalSpacing="md">
        <Table.Thead>
          <Table.Tr>
            {isLaminar && <Table.Th>Project</Table.Th>}
            <Table.Th>Key</Table.Th>
            <Table.Th>Source</Table.Th>
            <Table.Th w={48} />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          <Table.Tr>
            {isLaminar && <Table.Td><ProjectCell info={info} /></Table.Td>}
            <Table.Td>
              {info.hint
                ? <Text ff="monospace" size="sm" c="dimmed">{info.hint}</Text>
                : <Text size="sm" c="dimmed">No key yet</Text>}
            </Table.Td>
            <Table.Td><SourceBadge source={info.source} /></Table.Td>
            <Table.Td>
              {info.source === 'saved' && (
                <Tooltip label="Remove the saved key">
                  <ActionIcon variant="subtle" color="gray" aria-label="Remove the saved key" onClick={() => setRemoving(true)}>
                    <IconTrash size={ICON_SIZE} stroke={ICON_STROKE} />
                  </ActionIcon>
                </Tooltip>
              )}
            </Table.Td>
          </Table.Tr>
        </Table.Tbody>
      </Table>
      <SetKeyModal kind={kind} title={title} opened={setting} onClose={() => setSetting(false)}
        onSaved={() => { setSetting(false); onChanged(); }} />
      <RemoveKeyModal kind={kind} opened={removing} onClose={() => setRemoving(false)}
        onRemoved={() => { setRemoving(false); onChanged(); }} />
    </Stack>
  );
}

function ProjectCell({ info }) {
  if (!info.projectId) return <Text size="sm" c="dimmed">Not known yet</Text>;
  return (
    <Anchor href={info.projectUrl} target="_blank" rel="noreferrer" size="sm" ff="monospace">
      <Group gap={4} wrap="nowrap" component="span">
        {info.projectId.slice(0, 8)}
        <IconExternalLink size={14} />
      </Group>
    </Anchor>
  );
}

function SourceBadge({ source }) {
  const badge = SOURCE_BADGE[source];
  if (!badge) return <Badge color="yellow" variant="light">Not set</Badge>;
  return (
    <Tooltip label={badge.tip} multiline w={260}>
      <Badge color={badge.color} variant="light">{badge.label}</Badge>
    </Tooltip>
  );
}

// The dialog behind "API key": one field. The API checks the key with the provider first and answers
// with a sentence when it's refused, shown here as-is.
function SetKeyModal({ kind, title, opened, onClose, onSaved }) {
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const close = () => {
    setValue('');
    setError(null);
    onClose();
  };
  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await saveKey(kind, value);
      setValue('');
      onSaved();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal opened={opened} onClose={close} title={`Set ${title}`} centered>
      <form onSubmit={save}>
        <Stack gap="md">
          <PasswordInput label="API key" placeholder="Paste the key" value={value} autoComplete="off" data-autofocus
            onChange={(event) => setValue(event.currentTarget.value)} />
          <Text size="xs" c="dimmed">
            The key is checked with {kind === 'laminar' ? 'Laminar' : 'OpenAI'} before it&apos;s saved, then stored encrypted.
            Only its first and last 4 characters are shown afterwards.
          </Text>
          {error && <Alert color="red" variant="light">{error}</Alert>}
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={close}>Cancel</Button>
            <Button type="submit" loading={saving} disabled={!value.trim()}>Save</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}

function RemoveKeyModal({ kind, opened, onClose, onRemoved }) {
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState(null);

  const remove = async () => {
    setRemoving(true);
    setError(null);
    try {
      await clearKey(kind);
      onRemoved();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setRemoving(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Remove the saved key" centered>
      <Stack gap="md">
        <Text size="sm">
          Running locally, the key from .env is used again, if there is one. Without a key, signals wait until a new one is saved.
        </Text>
        {error && <Alert color="red" variant="light">{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={onClose}>Cancel</Button>
          <Button color="red" loading={removing} onClick={remove}>Remove</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
