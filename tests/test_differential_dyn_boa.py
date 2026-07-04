"""Differential tests for the DYNAMIC-argument contract (ERC20Dyn.vy).

Same shape as test_differential_boa.py, but every entry point exercised here
takes calldata the primitive encoders cannot build — ``DynArray`` batch
transfers, a ``Bytes[64]`` note, a struct (ABI tuple) argument. The dynamic
calldata driving both runtimes comes from ``tests/vectors/abi_lean_vectors.json``
— bytes produced out-of-process by evm-abi-lean's *verified* encoder
(``scripts/gen_abi_vectors.py``); ``tests/test_abi_vectors.py`` pins
:mod:`venom_opt.abi` to the same bytes. This is the end-to-end evidence that
the peephole (and its conservative constant-word memory tracking) is
behaviour-preserving on contracts with dynamic-ABI entry points, not just on
the primitive-only ERC-20.

Skipped automatically if titanoboa is not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

boa = pytest.importorskip("boa")  # skip the whole module if titanoboa is absent

from test_abi_vectors import load_vector_calldata  # noqa: E402

from venom_opt import balance_patch as bp  # noqa: E402
from venom_opt.erc20_abi import BALANCE_OF, MINT, arg_addr, word  # noqa: E402

ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "erc20dyn.json"
ONE = 10**18

#: verified calldata (evm-abi-lean encoder output), keyed by vector name
VEC = load_vector_calldata()

A = (0xAA).to_bytes(20, "big")
B = (0xBB).to_bytes(20, "big")
C = (0xCC).to_bytes(20, "big")
D = (0xDD).to_bytes(20, "big")


@pytest.fixture
def instances():
    """A fresh (original, patched) pair of deployed ERC20Dyn runtimes."""
    creation = bp.creation_from_artifact(ARTIFACT)
    runtime = bp.runtime_from_artifact(ARTIFACT)
    slot = bp.balance_slot_from_artifact(ARTIFACT)
    patched = bp.patch(runtime, slot)
    orig, _ = boa.env.deploy_code(bytecode=creation)
    opt, _ = boa.env.deploy_code(bytecode=creation)
    boa.env.set_code(opt, patched)
    return orig, opt


def _call(c, data: bytes, sender: bytes | None = None):
    return boa.env.raw_call(c, sender=sender, data=data)


def _bal(c, who: bytes) -> int:
    return int.from_bytes(_call(c, BALANCE_OF + arg_addr(who)).output, "big")


def _reverts(c, data: bytes, sender: bytes | None = None) -> bool:
    try:
        _call(c, data, sender=sender)
        return False
    except Exception:
        return True


# ----- patcher-level checks on the dynamic artifact ----------------------------


def test_dyn_artifact_patches():
    runtime = bp.runtime_from_artifact(ARTIFACT)
    slot = bp.balance_slot_from_artifact(ARTIFACT)
    assert slot == 6
    # 8 straight-line frame-0 sites + 2 loop-body sites at frame 0x20 / 0x60
    assert bp.count_sites(runtime, slot) == 10
    patched = bp.patch(runtime, slot)
    assert len(patched) == len(runtime)
    assert bp.count_sites(patched, slot) == 0
    # the allowance map (slot 7) stays untouched
    assert bp.count_sites(runtime, 7) == bp.count_sites(patched, 7)


# ----- success-path parity ------------------------------------------------------


def test_batch_transfer_parity(instances):
    """DynArray calldata: one batch fan-out, full balance map must match."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(10 * ONE))
        _call(c, VEC["batch_bcd_321"], sender=A)  # [B,C,D] / [3,2,1]*ONE
    for who in (A, B, C, D):
        assert _bal(orig, who) == _bal(opt, who), f"balance mismatch for {who.hex()}"
    assert _bal(orig, A) == 4 * ONE


def test_empty_batch_parity(instances):
    """Zero-length DynArrays: succeeds and moves nothing, identically."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(ONE))
        _call(c, VEC["batch_empty"], sender=A)
    assert _bal(orig, A) == _bal(opt, A) == ONE


def test_transfer_with_note_parity(instances):
    """Bytes calldata around static args, at both padding boundaries."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(5 * ONE))
        for name in ("note_empty", "note_gm", "note_full"):  # (B, ONE, <note>)
            _call(c, VEC[name], sender=A)
    assert _bal(orig, A) == _bal(opt, A) == 2 * ONE
    assert _bal(orig, B) == _bal(opt, B) == 3 * ONE


def test_struct_arg_parity(instances):
    """Struct (ABI tuple) calldata."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(5 * ONE))
        _call(c, VEC["pay_c_2"], sender=A)  # Payment(to=C, amount=2*ONE)
    assert _bal(orig, A) == _bal(opt, A) == 3 * ONE
    assert _bal(orig, C) == _bal(opt, C) == 2 * ONE


# ----- failure-path (revert) parity ----------------------------------------------


def test_batch_length_mismatch_reverts_identically(instances):
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(5 * ONE))
    data = VEC["batch_bc_1"]  # 2 receivers, 1 amount
    assert _reverts(orig, data, sender=A) is True
    assert _reverts(opt, data, sender=A) is True
    assert _bal(orig, A) == _bal(opt, A) == 5 * ONE


def test_batch_insufficient_balance_reverts_identically(instances):
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(ONE))
    data = VEC["batch_bc_11"]  # second leg underflows
    assert _reverts(orig, data, sender=A) is True
    assert _reverts(opt, data, sender=A) is True
    assert _bal(orig, A) == _bal(opt, A) == ONE
    assert _bal(orig, B) == _bal(opt, B) == 0


# ----- the point of the peephole ---------------------------------------------------


def test_batch_gas_not_worse(instances):
    """The batch loop hits balance keccaks per leg — the peephole savings scale."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(10 * ONE))
        _call(c, VEC["batch_b_1"], sender=A)  # warm the slots
    data = VEC["batch_bcd_111"]
    gO = _call(orig, data, sender=A).get_gas_used()
    gP = _call(opt, data, sender=A).get_gas_used()
    assert gP <= gO
