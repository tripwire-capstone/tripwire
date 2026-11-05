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

// Cluster colors, in order: the 1st cluster in a list gets the 1st color, and so on.
// Use clusterColor(i) so strips, charts and chips agree. Events in no cluster are gray.
export const CLUSTER_COLORS = ['violet.7', 'green.7', 'indigo.6', 'red.7', 'orange.7', 'cyan.7', 'pink.7', 'lime.7'];
export const UNCLUSTERED_COLOR = 'dark.3';
export const clusterColor = (i) => CLUSTER_COLORS[i % CLUSTER_COLORS.length];
// 'violet.7' -> 'var(--mantine-color-violet-7)', for plain CSS (backgrounds, borders).
export const cssColor = (mantineColor) => `var(--mantine-color-${mantineColor.replace('.', '-')})`;

// Run outcomes on the Runs chart.
export const RUN_COLORS = { event: 'teal.7', noEvent: 'dark.3', failed: 'red.7' };
