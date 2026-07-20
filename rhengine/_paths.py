"""
_paths.py — one robust resolver for app-owned files (secrets.json, config.json,
massive_cache, fred_cache, ...).

The engine is imported by consuming apps that own these files, so the historical
rule was "prefer the process cwd, else the engine's own folder." That breaks the
moment code runs from a subdirectory — e.g. a notebook launched from
`notebooks/` has cwd=`notebooks/`, so `secrets.json` (which lives in the repo
root) is invisible and every adapter raises "credentials missing."

app_path resolves in this order, returning the first that exists:
  1. cwd/name                          — the consuming-app convention
  2. name found by walking UP from cwd — a notebook in any subdir finds the root
  3. repo-root/name  (parent of this package dir) — the standalone-repo layout
  4. package-dir/name                  — legacy fallback (massive_cache lives here)
If none exist it returns cwd/name, so "file missing" errors still point at cwd.
"""

import os

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_PKG_DIR)


def app_path(name):
    candidates = [os.path.join(os.getcwd(), name)]

    # Walk up from cwd so a notebook/script in any subdir finds the repo file.
    d = os.path.abspath(os.getcwd())
    while True:
        candidates.append(os.path.join(d, name))
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent

    candidates.append(os.path.join(_REPO_ROOT, name))  # standalone-repo root
    candidates.append(os.path.join(_PKG_DIR, name))     # legacy (massive_cache)

    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]  # not found — keep pointing at cwd for error messages
