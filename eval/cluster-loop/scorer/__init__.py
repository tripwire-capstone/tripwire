"""Frozen cluster-quality scorer (phase 0 of the cluster loop). See eval/cluster-loop/README.md.

Importing this package puts signals/backend on sys.path (as eval/compare-laminar/compare.py does),
so `tripwire.clusterer.*` is importable from every module here.
"""

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
LOOP_DIR = PACKAGE_DIR.parent                      # eval/cluster-loop
REPO_ROOT = LOOP_DIR.parent.parent                 # ~/projects/tripwire
BACKEND_DIR = REPO_ROOT / "signals" / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
