import { Button } from '@mantine/core';
import { Link } from 'react-router-dom';
import Placeholder from '../components/Placeholder';

export default function TripwireList() {
  return (
    <Placeholder title="Tripwires" actions={<Button component={Link} to="/tripwires/new">New tripwire</Button>} shows={[
      "Table modeled on Laminar's Signals list: Status, Name, Events, Clusters, Activity, Last event, Runs, Created",
      'Search by name, time range (default 7 days)',
      'New tripwire button',
      'Click a row to open the tripwire',
    ]} />
  );
}
