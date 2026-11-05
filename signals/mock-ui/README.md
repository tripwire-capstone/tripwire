# Tripwire mock UI

A clickable mock of Tripwire's core screens, on fake data. Not connected to Laminar or our services.

```
cd mock-ui
npm install
npm run dev        # http://localhost:5174
```

- What each screen shows: `SPEC.md`. Rules for AI coding agents (and us): `AGENTS.md`.
- Visual reference with every shared component: http://localhost:5174/style
- Fake data (the Olympic story): `src/mock/data.js`. Pages read it through `useStoryData()`, so the
  **Story day** picker in the header (Day 1 / Day 3 / Day 6 / Today) changes every screen at once.
- Links between screens: `src/components/links.js`.

## How we work

1. Each page gets its own branch from the latest `main`, e.g. `mock-ui/tripwire-list`, and its own PR.
2. A page PR changes only its file in `src/pages/`. Shared files (`src/components/`, `src/mock/data.js`,
   `src/theme.js`, `src/App.jsx`, `package.json`) change only in their own small PR, merged first.
3. Review by running the branch and clicking through: `git fetch && git checkout <branch>`, then `npm install && npm run dev`.
4. Pull `main` into your branch at least once a day: `git pull origin main`.

Prompt for building a page (works for Claude Code and Codex):

> Read `mock-ui/AGENTS.md` and `mock-ui/SPEC.md`. Build the <screen> screen in `mock-ui/src/pages/<Page>.jsx`,
> using only components from `src/components/`, Mantine, and data from `src/mock/data.js`. Don't edit any other file.
> If you need a shared component or new data, stop and tell me.
