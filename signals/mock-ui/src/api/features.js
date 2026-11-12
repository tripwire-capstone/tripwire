// Controls hidden until the API supports them. HIDDEN_CONTROLS.md says why each one is hidden and
// what has to exist before it comes back. Turning one on: set it to true and delete its row there.
export const FEATURES = {
  clusterCounts: false, // Clusters column, cluster strip and colors, cluster filter
  activityColumn: false, // Activity sparkline on the Signals list
  deleteSignals: false, // Row checkboxes and Delete on the Signals list
  appVersions: false, // App version markers, filter and column
  templates: false, // Template tiles on the New signal form
  spanTrigger: false, // "When a specific span finishes"
  tokenFilter: false, // "Total tokens" filter
  spanAndTagFilters: false, // "Span names" and "Tags" filters
  statusNotEquals: false, // "does not equal" for the Status filter
  sampling: false, // Sampling slider
  backfill: false, // Backfill button, Settings → Backfill
  versionsTab: false, // Settings → Versions
  alertsTab: false, // Settings → Alerts
};
