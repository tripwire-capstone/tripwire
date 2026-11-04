# Tripwire mock UI: rules

Read this before changing anything in `mock-ui/`. What each screen shows is in `SPEC.md`.

- This is a clickable mock. No backend, no API calls. All data comes from `src/mock/data.js`.
- Read `SPEC.md` before building a page. Build only what it describes; ask instead of inventing features.
- Use Mantine components and `src/theme.js`. No hard-coded colors, font sizes or spacing.
- Conventions: main action = filled `Button`; secondary = `variant="light"`; tertiary = `variant="subtle" color="gray"`.
  Severity badges: `SeverityBadge`. Preset/custom badges: `KindBadge`. Times: `formatTime` / `formatDay` from `src/components/format.js`.
- Reuse components from `src/components/` and helpers from `src/mock/data.js` (counts, events per day, cluster tree).
  Don't re-count or copy data into a page.
- Don't edit `src/components/`, `src/mock/data.js`, `src/theme.js`, `src/App.jsx`, `package.json` or this file in a page PR.
  If a page needs a shared change, stop and say what you need; it goes in its own small PR first.
- Each page lives in `src/pages/<Page>.jsx`. Only edit the page you were asked to build. Replace its placeholder entirely.
- `src/pages/StyleSample.jsx` (route `/style`) is the visual reference. Pages should look like it.
- Words: Tripwire (a detector), tripwire event (one turn where it fired), cluster (a group of similar events).
  Never "signal" or "trip" in the UI.
- Dark mode only. Don't add new npm packages without asking.
- Run `npm run build` before saying a page is done; it must build without errors.
