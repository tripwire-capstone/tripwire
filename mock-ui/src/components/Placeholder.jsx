import { Paper, Text, List } from '@mantine/core';
import PageHeader from './PageHeader';

// Stand-in for a page that hasn't been built yet. Replace the whole page file when building it.
export default function Placeholder({ title, shows, actions }) {
  return (
    <>
      <PageHeader title={title} description="Not built yet. See SPEC.md." actions={actions} />
      <Paper withBorder p="lg" maw={720}>
        <Text fw={600} mb="xs">This screen will show</Text>
        <List size="sm" spacing={4}>
          {shows.map((s) => <List.Item key={s}>{s}</List.Item>)}
        </List>
      </Paper>
    </>
  );
}
