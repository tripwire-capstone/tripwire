import Placeholder from '../components/Placeholder';

export default function Notifications() {
  return (
    <Placeholder title="Notifications" shows={[
      'In-app list of both alert types: new cluster, critical event',
      'Alert settings per tripwire: new cluster, new critical event, skip similar events, Slack',
      'A mock Slack message',
    ]} />
  );
}
