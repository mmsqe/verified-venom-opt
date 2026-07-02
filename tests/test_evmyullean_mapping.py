"""Drift guard for the end-to-end soundness map (README "full statement" table).

The in-repo Lean proof (``verification/VenomOpt/Peephole.lean``) is the
*slot-injectivity* core. The end-to-end claim — that the rewritten EVM bytecode
computes the same storage as the original, against EVMYulLean's real ``EVM.step``
— lives in the sibling EVMYulLean development, and the README maps to specific
theorems there. If one of those is renamed or removed, the README's end-to-end
map silently rots and nothing fails. This test pins the map: it asserts each
referenced theorem still exists under its name in its file, and reports the
EVMYulLean commit the map was validated against (provenance).

It is **skipped** when EVMYulLean is not checked out alongside this repo (so the
repo stays self-contained for CI without the sibling). Point it explicitly with
the ``EVMYULLEAN_DIR`` environment variable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# (theorem name, EVMYulLean file under EvmYul/Venom/) — mirrors README's
# "full, end-to-end statement (EVMYulLean)" table.
MAPPING = [
    ("distinct_addresses_distinct_opt_slots", "NoAlias.lean"),
    ("realizes_write_opt", "SlotAbstraction.lean"),
    ("write_opt_preserves_named", "SlotAbstraction.lean"),
    ("venomBalanceLoad_orig_opt_equiv", "BalanceSlot.lean"),
    ("transfer_preserves_solvent", "Solvency.lean"),
    # ERC-20 spec preservation — the README table's ``Erc20.*``: each token
    # operation preserves solvency (Σ balances = totalSupply), so the ~addr
    # rewrite keeps the ERC-20 spec.
    ("doApprove_solvent", "Erc20.lean"),
    ("doTransfer_solvent", "Erc20.lean"),
    ("doTransferFrom_solvent", "Erc20.lean"),
]


def _evmyullean_root() -> Path:
    env = os.environ.get("EVMYULLEAN_DIR")
    if env:
        return Path(env)
    # default: sibling checkout next to this repo
    return Path(__file__).resolve().parents[2] / "EVMYulLean"


def _evmyullean_sha(root: Path) -> str | None:
    """Short SHA of the EVMYulLean checkout being validated, or None if not a git repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


@pytest.fixture(scope="module")
def venom_dir() -> Path:
    d = _evmyullean_root() / "EvmYul" / "Venom"
    if not d.is_dir():
        pytest.skip(f"EVMYulLean not found at {d} (set EVMYULLEAN_DIR to enable the map guard)")
    return d


@pytest.mark.parametrize("name,basename", MAPPING, ids=[m[0] for m in MAPPING])
def test_mapped_theorem_exists(venom_dir: Path, name: str, basename: str):
    f = venom_dir / basename
    assert f.is_file(), f"EVMYulLean file missing: {f} (README map drifted)"
    text = f.read_text()
    assert f"theorem {name}" in text, (
        f"theorem {name} not found in {basename} — the README end-to-end map has drifted"
    )


def test_provenance_recorded(venom_dir: Path):
    """Report the EVMYulLean commit the map was validated against (provenance)."""
    sha = _evmyullean_sha(_evmyullean_root())
    if sha is None:
        pytest.skip("EVMYulLean checkout is not a git repo — no provenance SHA available")
    print(f"\nmap guard validated against EVMYulLean @ {sha}")
    assert sha, "empty EVMYulLean provenance SHA"
