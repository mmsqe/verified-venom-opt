"""The *verified-opt* facet: run the machine-checked soundness proof.

The third pillar of the pipeline (alongside ``balance_patch`` — the *balance*
rewrite — and the *peephole* package/CLI). Makes "is this optimization verified?"
a programmatic call, not just a Lean directory: it shells out to ``lake build``
on the mathlib-free proof in ``verification/`` and reports whether it checks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: The Lean verification project (repo-root sibling of ``src/``).
VERIFICATION_DIR = Path(__file__).resolve().parents[2] / "verification"

#: The soundness theorems machine-checked there (standard axioms only).
THEOREMS = (
    "optSlot_injective",
    "distinct_addresses_distinct_opt_slots",
    "write_no_alias",
    "read_own_write",
    "unpatched_read_misses_patched_write",
)


def verify(verification_dir: str | Path | None = None) -> bool:
    """Build (machine-check) the soundness proof. Returns True iff it checks."""
    d = Path(verification_dir or VERIFICATION_DIR)
    if not (d / "lakefile.toml").exists():
        raise FileNotFoundError(f"no Lean project at {d}")
    return subprocess.run(["lake", "build"], cwd=d).returncode == 0
