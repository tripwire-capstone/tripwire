#!/usr/bin/env bash
# One-time setup for tau-traffic: downloads tau2-bench at the version we tested,
# installs its Python environment, and creates .env from .env.example.
# Safe to run again.
set -euo pipefail

TAU2_REPO="https://github.com/sierra-research/tau2-bench"
TAU2_COMMIT="033bf082f48e4a3c6a1256208e93cf2ce9c3bb6f"  # Nov 4, 2026; update deliberately

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

for cmd in git uv; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$cmd is not installed. See README.md (Requirements)." >&2
    exit 1
  fi
done

if [[ -z "${TAU2_BENCH_DIR:-}" && -f .env ]]; then
  TAU2_BENCH_DIR="$(sed -n 's/^[[:space:]]*TAU2_BENCH_DIR[[:space:]]*=[[:space:]]*//p' .env | tail -n 1 | tr -d "\"'")"
fi

if [[ -n "${TAU2_BENCH_DIR:-}" ]]; then
  # Your own clone: use it as it is, just warn if it's a different version.
  dir="${TAU2_BENCH_DIR/#\~/$HOME}"
  if [[ ! -d "$dir/src/tau2" ]]; then
    echo "TAU2_BENCH_DIR is set to $dir, but tau2-bench isn't there." >&2
    exit 1
  fi
  # Fine if the tested version is in its history (e.g. with your own commits on top).
  if ! git -C "$dir" merge-base --is-ancestor "$TAU2_COMMIT" HEAD 2>/dev/null; then
    current="$(git -C "$dir" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "Note: $dir is at $current, which doesn't include the version tau-traffic was"
    echo "tested with (${TAU2_COMMIT:0:7}). It may still work; if not, check out that version."
  fi
else
  dir="$HERE/.tau2-bench"
  if [[ -d "$dir/.git" ]] && [[ "$(git -C "$dir" rev-parse HEAD)" == "$TAU2_COMMIT" ]]; then
    echo "tau2-bench is already downloaded."
  else
    echo "Downloading tau2-bench (about 1 GB, a few minutes)..."
    mkdir -p "$dir"
    git -C "$dir" init --quiet
    git -C "$dir" remote remove origin 2>/dev/null || true
    git -C "$dir" remote add origin "$TAU2_REPO"
    git -C "$dir" fetch --quiet --depth 1 origin "$TAU2_COMMIT"
    git -C "$dir" checkout --quiet --force FETCH_HEAD
  fi
fi

echo "Installing tau2-bench's Python environment..."
uv sync --quiet --project "$dir" --extra knowledge
# Fetch the tool's own extras now, so the first run doesn't pause for them.
uv run --quiet --project "$dir" --extra knowledge --with "lmnr==0.8.9" --with questionary \
  python -c "import lmnr, questionary"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi

echo
if grep -Eq '^[[:space:]]*OPENAI_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]' .env \
   && grep -Eq '^[[:space:]]*LMNR_PROJECT_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]' .env; then
  echo "Done. Next: ./tau-traffic"
else
  echo "Done. Next: add your OPENAI_API_KEY and LMNR_PROJECT_API_KEY to .env, then run ./tau-traffic"
fi
