import { Anchor } from '@mantine/core';
import { laminarTraceUrl } from '../mock/data';

// "Open in Laminar" link for one trace. Opens in a new tab.
export default function LaminarLink({ traceId, children = 'Open in Laminar ↗' }) {
  return (
    <Anchor href={laminarTraceUrl(traceId)} target="_blank" rel="noreferrer" size="sm" style={{ whiteSpace: 'nowrap' }}>
      {children}
    </Anchor>
  );
}
