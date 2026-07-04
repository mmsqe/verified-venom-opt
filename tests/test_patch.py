"""Unit tests for the balance-slot peephole patcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from venom_opt import balance_patch as bp

ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "erc20.json"


@pytest.fixture
def runtime() -> bytes:
    return bp.runtime_from_artifact(ARTIFACT)


@pytest.fixture
def slot() -> int:
    return bp.balance_slot_from_artifact(ARTIFACT)


def test_layout_slot(slot: int):
    # name/symbol are String[32] (2 slots each) + decimals + totalSupply push
    # balanceOf to slot 6, allowance to 7.
    assert slot == 6


def test_sites_present(runtime: bytes, slot: int):
    assert bp.count_sites(runtime, slot) == 6


def test_patch_length_preserving(runtime: bytes, slot: int):
    patched = bp.patch(runtime, slot)
    assert len(patched) == len(runtime)


def test_patch_removes_all_sites(runtime: bytes, slot: int):
    patched = bp.patch(runtime, slot)
    assert bp.count_sites(patched, slot) == 0


def test_patch_changes_three_bytes_per_site(runtime: bytes, slot: int):
    patched = bp.patch(runtime, slot)
    changed = sum(a != b for a, b in zip(runtime, patched))
    assert changed == 3 * bp.count_sites(runtime, slot)  # keccak tail -> MLOAD/NOT


def test_patch_idempotent(runtime: bytes, slot: int):
    # patching an already-patched runtime finds no sites (and raises, as designed)
    patched = bp.patch(runtime, slot)
    with pytest.raises(ValueError, match="no balance sites"):
        bp.patch(patched, slot)


def test_allowance_slot_untouched(runtime: bytes, slot: int):
    # allowance lives at slot 7 (nested map); patching balanceOf (slot 6) must
    # not touch those keccak sites — the whole soundness condition.
    import json

    allowance_slot = json.loads(ARTIFACT.read_text())["storageLayout"]["allowance"]["slot"]
    before = bp.count_sites(runtime, allowance_slot)
    patched = bp.patch(runtime, slot)
    after = bp.count_sites(patched, allowance_slot)
    assert before == after == 3  # allowance sites survive unchanged


def test_wrong_slot_raises(runtime: bytes):
    # slot 2 (the old Snekmate default) has no sites in this contract
    with pytest.raises(ValueError, match="no balance sites"):
        bp.patch(runtime, 2)


def test_patch_creation_embeds(slot: int):
    creation = bp.creation_from_artifact(ARTIFACT)
    runtime = bp.runtime_from_artifact(ARTIFACT)
    patched_creation = bp.patch_creation(creation, runtime, slot)
    assert len(patched_creation) == len(creation)
    # the patched runtime is embedded verbatim in the patched creation code
    assert bp.patch(runtime, slot) in patched_creation


# ----- mem[0x00] tracker conservatism (synthetic bytecode) --------------------
#
# The tracker is a linear scan; anything that can clobber mem[0x00] — or a
# basic-block boundary, where the linear predecessor is not the dynamic one —
# must reset it to unknown, or a later keccak shape could be wrongly patched.

STORE_SLOT = bytes([0x60, 0x06, 0x5F, 0x52])  # PUSH1 6 PUSH0 MSTORE  (mem[0]=6)
FULL_SITE = bytes([0x60, 0x20, 0x52, 0x60, 0x40, 0x5F, 0x20])  # the full keccak shape


def test_tracker_baseline_counts_site():
    assert bp.count_sites(STORE_SLOT + FULL_SITE, 6) == 1


@pytest.mark.parametrize(
    "clobber",
    [
        pytest.param(bytes([0x37]), id="CALLDATACOPY"),
        pytest.param(bytes([0x3E]), id="RETURNDATACOPY"),
        pytest.param(bytes([0x5E]), id="MCOPY"),
        pytest.param(bytes([0xFA]), id="STATICCALL"),
        pytest.param(bytes([0x52]), id="computed-offset MSTORE"),
        pytest.param(bytes([0x60, 0x00, 0x52]), id="PUSH1 0 MSTORE"),
        pytest.param(bytes([0x60, 0x1F, 0x53]), id="MSTORE8 at 31 (overlaps word)"),
        pytest.param(bytes([0x5B]), id="JUMPDEST (block boundary)"),
    ],
)
def test_tracker_resets_on_clobber(clobber: bytes):
    assert bp.count_sites(STORE_SLOT + clobber + FULL_SITE, 6) == 0


@pytest.mark.parametrize(
    "benign",
    [
        pytest.param(bytes([0x60, 0x40, 0x52]), id="PUSH1 0x40 MSTORE (past word)"),
        pytest.param(bytes([0x61, 0x01, 0x00, 0x52]), id="PUSH2 0x100 MSTORE"),
        pytest.param(bytes([0x60, 0x20, 0x53]), id="MSTORE8 at 32"),
        pytest.param(bytes([0x80, 0x50]), id="DUP1 POP (no memory effect)"),
    ],
)
def test_tracker_survives_benign_ops(benign: bytes):
    assert bp.count_sites(STORE_SLOT + benign + FULL_SITE, 6) == 1
