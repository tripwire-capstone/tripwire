import { Badge } from '@mantine/core';
import { SEVERITY_COLOR, KIND_COLOR, NOTIFICATION_LABEL } from '../theme';

// Small labels used on several screens. Colors come from theme.js conventions.

export function SeverityBadge({ severity }) {
  return <Badge color={SEVERITY_COLOR[severity]} variant="light" style={{ flexShrink: 0, overflow: 'visible' }}>{severity}</Badge>;
}

export function KindBadge({ kind, size = 'sm' }) {
  return <Badge color={KIND_COLOR[kind]} variant="outline" size={size}>{kind}</Badge>;
}

export function StatusBadge({ status }) {
  return <Badge color={status === 'active' ? 'teal' : 'gray'} variant="light">{status}</Badge>;
}

