import { createTheme } from '@mantine/core';

// The only shared styling decisions. Everything else is Mantine's default.
// Dark mode only, close to Laminar's near-black UI.
export const theme = createTheme({
  primaryColor: 'teal',   // accent: main buttons, links, active nav
  defaultRadius: 'md',    // corner roundness everywhere
  colors: {
    // Mantine's dark scale, shifted darker. [7] = page background, [6] = surfaces, [4] = borders.
    dark: ['#d4d4d4', '#b5b5b5', '#8f8f8f', '#6b6b6b', '#3a3a3a', '#2b2b2b', '#1a1a1a', '#0f0f0f', '#0a0a0a', '#050505'],
  },
});

// Conventions both AIs follow (also listed in AGENTS.md).
export const SEVERITY_COLOR = { critical: 'red', warning: 'yellow', info: 'gray' };
export const KIND_COLOR = { preset: 'blue', custom: 'grape' };
export const NOTIFICATION_LABEL = { 'new-cluster': 'New cluster', 'critical-event': 'Critical event' };
