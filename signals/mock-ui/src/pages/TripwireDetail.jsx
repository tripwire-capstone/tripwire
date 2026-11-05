import { useParams } from 'react-router-dom';
import Placeholder from '../components/Placeholder';
import { getTripwire } from '../mock/data';

export default function TripwireDetail() {
  const { id } = useParams();
  const tripwire = getTripwire(id);
  return (
    <Placeholder title={tripwire ? tripwire.name : 'Tripwire'} shows={[
      'Tripwire events over time',
      'Cluster tree: broad clusters that open into sub-clusters; click one to filter',
      'Tripwire events table: severity, payload fields, time, app version filter, Open in Laminar',
    ]} />
  );
}
