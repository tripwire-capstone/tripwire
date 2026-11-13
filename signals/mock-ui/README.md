# Tripwire UI

Tripwire's screens: Home, the Signals list, a signal's page (Events, Runs, Settings) and the New signal form.
Every page reads the real Tripwire API (no fake data). How the pages use the API and how to run them with the
backend: `SIGNALS_API.md`.

```
cd signals/mock-ui
npm install
npm run dev        # http://localhost:5174 (needs the backend: docker compose up in signals/backend)
```

- What each screen shows: `SPEC.md`. Rules for AI coding agents (and us): `AGENTS.md`.
- How the pages use the API: `SIGNALS_API.md`. Controls hidden until the API supports them: `HIDDEN_CONTROLS.md`.
- Links between screens: `src/components/links.js`.

## How we work

1. Each change gets its own branch from the latest `main` and its own PR.
2. A page PR changes only its file in `src/pages/`. Shared files (`src/components/`, `src/api/`, `src/theme.js`,
   `src/App.jsx`, `package.json`) change only in their own small PR, merged first.
3. Review by running the branch and clicking through: `git fetch && git checkout <branch>`, then `npm install && npm run dev` in `signals/mock-ui`.
4. Pull `main` into your branch at least once a day: `git pull origin main`.
