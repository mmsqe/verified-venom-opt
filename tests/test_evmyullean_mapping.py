"""Drift guard for the end-to-end soundness map (README "full statement" table).

The in-repo Lean proof (``verification/VenomOpt/Peephole.lean``) is the
*slot-injectivity* core. The end-to-end claim — that the rewritten EVM bytecode
computes the same storage as the original, against EVMYulLean's real ``EVM.step``
— lives in the sibling EVMYulLean development, and the README maps to specific
theorems there. If one of those is renamed or removed, the README's end-to-end
map silently rots and nothing fails. This test pins the map: it asserts each
referenced theorem still exists under its name in its file, and reports the
EVMYulLean commit the map was validated against (provenance).

Three soundness pillars are guarded against EVMYulLean, one against evm-abi-lean:

* ``MAPPING`` — the *balance-patch* chain: the ``~addr`` storage rewrite preserves
  storage semantics against real ``EVM.step`` (slot injectivity → write-through →
  load equivalence → solvency / ERC-20 spec).
* ``CODEGEN_MAPPING`` — the *Venom→EVM codegen* chain: the generated EVM program
  simulates the source Venom function (``codegen_correct`` / ``codegen_fn_correct``,
  non-vacuously), block-by-block, so the compiled output of the (optimized) Venom
  is itself proven correct.
* ``ABI_MAPPING`` — the *dynamic-ABI* chain in EVMYulLean: the calldata shapes the
  dynamic differential tests drive (``tests/test_differential_dyn_boa.py``:
  DynArray / Bytes / struct) are the shapes EVMYulLean cross-validates in-build
  against the verified evm-abi-lean encoder (``lake build AbiCrossval``).
* ``ABI_LEAN_MAPPING`` — the *verified-encoder* pillar in the evm-abi-lean sibling:
  the ABI encode/decode roundtrip theorems (dynamic arrays / tuples / nested
  structs included). :mod:`venom_opt.abi` mirrors those layouts; ``eth_abi``
  cross-checks them executably (``tests/test_abi.py``), these pin them
  mathematically.

Each half is **skipped** when its sibling checkout (EVMYulLean / evm-abi-lean)
is absent (so the repo stays self-contained for CI without the siblings). Point
them explicitly with the ``EVMYULLEAN_DIR`` / ``EVMABILEAN_DIR`` environment
variables.
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

# (theorem name, EVMYulLean file relative to EvmYul/Venom/) — the Venom->EVM
# codegen-correctness pillar: the generated program simulates the source Venom
# function, so the compiled output of the (optimized) Venom is proven correct.
CODEGEN_MAPPING = [
    # top-level: the generated program simulates the Venom function / context...
    ("codegen_fn_correct", "Hol/Codegen/CodegenCorrectness.lean"),
    ("codegen_correct", "Hol/Codegen/CodegenCorrectness.lean"),
    # ...and it is non-vacuous (a real generated program, not an empty witness).
    ("codegenFuel_correct_nonvacuous_witness", "Hol/Codegen/CodegenCorrectness.lean"),
    # block-level simulation capstone the above rests on.
    ("genBlockSimulation", "Hol/Codegen/GenBlockSim.lean"),
    # variable-arity instruction milestone: LOG end-to-end through the generator.
    ("genRegularInstPlan_log_sim", "Hol/Codegen/GenInstSim.lean"),
]

# (theorem name, EVMYulLean file relative to EvmYul/Venom/) — the dynamic-ABI
# pillar: selector agreement plus decode-equivalence for the exact argument
# families the dynamic differential tests exercise (static transfer args, a
# DynArray, mixed static/dynamic), and the ABI front-end entry→dispatch→return
# fact they compose into.
ABI_MAPPING = [
    ("erc20_selectors_match_keccak", "AbiCrossval.lean"),
    ("abiLean_transfer_decodes", "AbiCrossval.lean"),
    ("abiLean_dynarray_sum", "AbiCrossval.lean"),
    ("abiLean_mixed_sum", "AbiCrossval.lean"),
    ("genABIFrontEnd_entry_returns", "AbiEndToEnd.lean"),
    # the composed capstones: a balanceOf CALL (read path) and a transfer CALL
    # (write path) halt with identical returndata on the original (keccak
    # slot) and patched (~addr) dispatchers under the storage relation; the
    # transfer one also PRESERVES the relation, so multi-call traces stay
    # equal -- the proof-level counterpart of the differential tests.
    ("abi_balanceOf_orig_opt_returndata_eq", "AbiBalance.lean"),
    ("abi_transfer_orig_opt_equiv", "AbiTransfer.lean"),
    ("transferStorage_rel", "AbiTransfer.lean"),
]

# (theorem name, file relative to the evm-abi-lean repo root) — the
# verified-encoder pillar: the encode→decode roundtrip capstones over all
# well-formed ABI types (nested tuples/structs included), the mathematical
# reference for the layouts venom_opt.abi implements.
ABI_LEAN_MAPPING = [
    ("roundtrip_wf", "EvmAbi/Roundtrip.lean"),
    ("roundtrip_args_wff", "EvmAbi/Roundtrip.lean"),
]


def _evmyullean_root() -> Path:
    env = os.environ.get("EVMYULLEAN_DIR")
    if env:
        return Path(env)
    # default: sibling checkout next to this repo
    return Path(__file__).resolve().parents[2] / "EVMYulLean"


def _git_sha(root: Path) -> str | None:
    """Short SHA of the checkout being validated, or None if not a git repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
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


def _assert_theorem(base: Path, name: str, relpath: str, repo: str = "EVMYulLean") -> None:
    f = base / relpath
    assert f.is_file(), f"{repo} file missing: {f} (map drifted)"
    assert f"theorem {name}" in f.read_text(), f"theorem {name} not found in {relpath} — the {repo} map has drifted"


@pytest.mark.parametrize("name,basename", MAPPING, ids=[m[0] for m in MAPPING])
def test_mapped_theorem_exists(venom_dir: Path, name: str, basename: str):
    _assert_theorem(venom_dir, name, basename)


@pytest.mark.parametrize("name,relpath", CODEGEN_MAPPING, ids=[m[0] for m in CODEGEN_MAPPING])
def test_codegen_theorem_exists(venom_dir: Path, name: str, relpath: str):
    _assert_theorem(venom_dir, name, relpath)


@pytest.mark.parametrize("name,relpath", ABI_MAPPING, ids=[m[0] for m in ABI_MAPPING])
def test_abi_theorem_exists(venom_dir: Path, name: str, relpath: str):
    _assert_theorem(venom_dir, name, relpath)


def test_provenance_recorded(venom_dir: Path):
    """Report the EVMYulLean commit the map was validated against (provenance)."""
    sha = _git_sha(_evmyullean_root())
    if sha is None:
        pytest.skip("EVMYulLean checkout is not a git repo — no provenance SHA available")
    print(f"\nmap guard validated against EVMYulLean @ {sha}")
    assert sha, "empty EVMYulLean provenance SHA"


# ----- the evm-abi-lean (verified encoder) half -------------------------------


def _abilean_root() -> Path:
    env = os.environ.get("EVMABILEAN_DIR")
    if env:
        return Path(env)
    # sibling checkout next to this repo, else the pinned-commit clone that
    # scripts/gen_abi_vectors.py maintains in the cache
    sibling = Path(__file__).resolve().parents[2] / "evm-abi-lean"
    if (sibling / "EvmAbi").is_dir():
        return sibling
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return cache / "verified-venom-opt" / "abi-lean"


@pytest.fixture(scope="module")
def abilean_dir() -> Path:
    d = _abilean_root()
    if not (d / "EvmAbi").is_dir():
        pytest.skip(f"evm-abi-lean not found at {d} (set EVMABILEAN_DIR to enable the ABI guard)")
    return d


@pytest.mark.parametrize("name,relpath", ABI_LEAN_MAPPING, ids=[m[0] for m in ABI_LEAN_MAPPING])
def test_abilean_theorem_exists(abilean_dir: Path, name: str, relpath: str):
    _assert_theorem(abilean_dir, name, relpath, repo="evm-abi-lean")


def test_abilean_provenance_recorded(abilean_dir: Path):
    """Report the evm-abi-lean commit the ABI map was validated against."""
    sha = _git_sha(abilean_dir)
    if sha is None:
        pytest.skip("evm-abi-lean checkout is not a git repo — no provenance SHA available")
    print(f"\nABI map guard validated against evm-abi-lean @ {sha}")
    assert sha, "empty evm-abi-lean provenance SHA"
