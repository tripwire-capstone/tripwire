import { Group, Title, Text, Breadcrumbs, Anchor } from '@mantine/core';
import { Link } from 'react-router-dom';

// Title row at the top of every page. `crumbs` is an optional [{ label, to }] trail,
// `actions` sits on the right (e.g. a "New tripwire" button).
export default function PageHeader({ title, description, crumbs, actions }) {
  return (
    <div style={{ marginBottom: 'var(--mantine-spacing-lg)' }}>
      {crumbs && (
        <Breadcrumbs mb="xs">
          {crumbs.map((c) => (c.to
            ? <Anchor key={c.label} component={Link} to={c.to} size="sm">{c.label}</Anchor>
            : <Text key={c.label} size="sm" c="dimmed">{c.label}</Text>))}
        </Breadcrumbs>
      )}
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <div style={{ minWidth: 0 }}>
          <Title order={2}>{title}</Title>
          {description && <Text c="dimmed" mt={4} maw={720}>{description}</Text>}
        </div>
        {actions && <Group gap="sm">{actions}</Group>}
      </Group>
    </div>
  );
}
