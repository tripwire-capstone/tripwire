import Placeholder from '../components/Placeholder';

export default function Home() {
  return (
    <Placeholder title="Home" shows={[
      'Tripwires that are spiking',
      'Newly formed clusters',
      'Recent critical tripwire events',
      'Unread notifications',
      'Each item links to its tripwire or cluster',
    ]} />
  );
}
