import { Anchor } from '@mantine/core';
import { laminarTraceUrl } from './links';

// "Open in Laminar" link for one trace. Opens in a new tab.
export default function LaminarLink({ traceId, children = 'Open in Laminar ↗', onClick }) {
  return (
    <Anchor href={laminarTraceUrl(traceId)} onClick={onClick} target="_blank" rel="noreferrer" size="sm" style={{ whiteSpace: 'nowrap' }}>
      {children}
    </Anchor>
  );
}
