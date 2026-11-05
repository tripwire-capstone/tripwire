import { Button } from '@mantine/core';
import { Link } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import { homeUrl } from '../components/links';

export default function NotFound() {
  return (
    <PageHeader title="Page not found" description="This page doesn't exist in the mock."
      actions={<Button component={Link} to={homeUrl()}>Go home</Button>} />
  );
}
