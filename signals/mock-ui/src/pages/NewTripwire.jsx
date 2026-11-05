import Placeholder from '../components/Placeholder';

export default function NewTripwire() {
  return (
    <Placeholder title="New tripwire" shows={[
      'Start from a preset or blank',
      'Name and plain-language instruction',
      'Payload fields (name + description each)',
      'Trigger filters and sample rate',
      'Test on a sample trace',
      'Run on past traces (backfill)',
    ]} />
  );
}
