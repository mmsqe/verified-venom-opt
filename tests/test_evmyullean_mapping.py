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
* ``ABI_LEAN_MAPPING`` — the *verified-encoder* pillar in the evm-abi-lean sibling
  (the codec roundtrip only; the argument level and the kernel-reducibility
  bridge moved into EVMYulLean and are guarded by ``ABI_MAPPING`` above):
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
import re
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
    ("genBlockSimulation", "Hol/Codegen/GenBlockSim/CfgHfsim.lean"),
    # variable-arity instruction milestone: LOG end-to-end through the generator.
    ("genRegularInstPlan_log_sim", "Hol/Codegen/GenInstSim/JoinProducers.lean"),
    # --- the RECIPE route (2026-07) -------------------------------------------
    # The driver that makes the claim general: it reduces an ARBITRARY function's
    # codegen_correct to a per-block obligation, so the chain above is no longer
    # tied to hand-built examples.
    ("codegen_correct_ofBlocks_recipeW", "Hol/Codegen/GenBlockSimExample/RecipeWalk.lean"),
    # ...and its invariant-carrying form, which is what lets a recipe depend on
    # VALUES (not just shapes) — required by the RETURN/REVERT capstones.
    ("codegen_correct_ofBlocks_recipeW_inv", "Hol/Codegen/GenBlockSimExample/RecipeWalk.lean"),
    # Terminator coverage on REAL generated programs. DJMP is the hardest (its
    # dispatch mints trampoline labels); pinning it guards the whole 8/8 claim.
    ("codegen_correct_djFn_recipeW", "Hol/Codegen/GenBlockSimExample/RecipeSlices.lean"),
    # SYMBOLIC-size RETURN/REVERT: the call value is only BOUNDED, not pinned to
    # zero, so the memory operands are genuinely symbolic. These carry the first
    # invariant in that development to constrain the ASM side.
    ("codegen_correct_rFn_symbolic", "Hol/Codegen/GenBlockSimExample/RecipeSlices.lean"),
    ("codegen_correct_tFn_symbolic", "Hol/Codegen/GenBlockSimExample/RecipeSlices.lean"),
    # Non-vacuity for the above: the statement has a `| _ => True` catch-all, so
    # the REAL arm must be shown to fire — here for EVERY non-halted state.
    ("rFn_reverts_sym", "Hol/Codegen/GenBlockSimExample/RecipeSlices.lean"),
    # Scope honesty: RET is codegen-ready and COMPILED, but its IntRet lands in
    # that catch-all, so codegen_correct is VACUOUSLY true for RET-terminated
    # functions. This theorem proves that, and pinning it stops the 8/8 claim
    # from being read as "all ten isTerminator opcodes".
    ("codegen_correct_retFn_vacuous", "Hol/Codegen/GenBlockSimExample/RecipeSlices.lean"),
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
    # dynamic-keyed maps: the outer-keccak -> ~innerHash rewrite is invisible
    # at the ABI-call level with the relation quantified over the inner-hash
    # value -- no collision-freedom hypothesis at all (Tier B).
    ("abi_dynKeyGet_orig_opt_returndata_eq", "AbiDynKey.lean"),
    # the multi-map boundary, machine-checked: packSlot (map-id packing for
    # address keys, the future multi-map scheme) is jointly injective; and the
    # impossibilities -- two full-word-key maps / >32-byte keys MUST alias
    # (pigeonhole), so one-optimized-map-per-contract is forced, not a tool gap.
    ("packSlot_injective", "SlotPacking.lean"),
    ("two_fullword_maps_must_alias", "SlotPacking.lean"),
    ("long_keys_must_alias", "SlotPacking.lean"),
    # multi-slot VALUES: ~(key*stride + off) makes value windows abut instead
    # of interleaving -- the sound layout the tool-side value-type guard
    # points to (plain ~key + off corrupts adjacent keys deterministically).
    ("strideSlot_injective", "SlotPacking.lean"),
    # execution-level non-interference of the IR pass's packSlot blocks: two
    # optimized maps in one contract never alias (the case ~key can't do) --
    # the Lean counterpart of the multi-map differential test (test_ir_pass).
    ("packSlot_cross_map_noninterference", "AbiMultiMap.lean"),
    ("packSlot_load_own_write", "AbiMultiMap.lean"),
    # multi-word VALUE maps: the strideSlot IR pass's actual output
    # (sub (~(mul stride key)) off) equals strideSlot, whose distinct
    # (key,field) pairs never overlap -- the case ~key corrupts.
    ("venomStride_eq_strideSlot", "AbiMultiMap.lean"),
    ("strideSlot_field_noninterference", "AbiMultiMap.lean"),
    # The argument level and the kernel-reducibility bridge live HERE, not in
    # evm-abi-lean: that library's scope is the codec roundtrip, and ``encodeArgs``
    # is definitionally the tuple level, so ``roundtrip_args`` is a one-line
    # corollary. ``encode``/``decode`` are well-founded-recursive over ``Ty``
    # (``Acc.rec``, kernel-opaque), so concrete encodings do not reduce under
    # ``decide +kernel``; ``CodecEval`` adds a fuel-indexed structural mirror plus
    # these two equalities, which is what lets ``AbiCrossval`` check concrete
    # calldata on base axioms. Pin them: if they go, that target silently
    # regresses to a ``native_decide`` computational axiom.
    ("roundtrip_args", "AbiLean/Args.lean"),
    ("encodeF_eq_encode", "AbiLean/CodecEval.lean"),
    ("encodeArgs_eq_encodeF", "AbiLean/CodecEval.lean"),
]

# (theorem name, file relative to the evm-abi-lean repo root) — the
# verified-encoder pillar: the encode→decode roundtrip capstones over all
# well-formed ABI types (nested tuples/structs included), the mathematical
# reference for the layouts venom_opt.abi implements.
ABI_LEAN_MAPPING = [
    # The Ty-indexed rewrite replaced the ABIType codec (whose roundtrips carried
    # explicit well-formedness hypotheses, hence the ``_wf`` / ``_wff`` suffixes)
    # with a type-indexed value family: ``t.Val`` is already refined, so the
    # statement is plain ``decode t (encode t v) = some v``. Both capstones now
    # live beside the codec itself.
    ("roundtrip", "EvmAbi/Codec.lean"),
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
    # Word-boundary match, not a substring one: several mapped names are prefixes
    # of their neighbours (``roundtrip`` / ``roundtrip_args``), so a plain ``in``
    # would let a deleted theorem pass on the strength of a longer sibling.
    pat = re.compile(rf"^theorem {re.escape(name)}\b", re.MULTILINE)
    assert pat.search(f.read_text()), f"theorem {name} not found in {relpath} — the {repo} map has drifted"


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
