"""Differential tests: the patched runtime behaves identically to the original.

Deploys the ERC-20 twice via its creation code, etches the peephole-patched
runtime onto the second instance, then exercises mint / transfer / approve /
transferFrom / balanceOf / allowance and asserts **behavioural parity** between
the original (keccak slot) and the patched (`~addr` slot) runtimes — including
revert parity for the failure paths. The allowance path (a nested map at slot 7)
is deliberately left unpatched, so these tests also confirm the peephole touches
*only* the balance slot. Plus the gas delta — the point of the peephole.

Skipped automatically if titanoboa is not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

boa = pytest.importorskip("boa")  # skip the whole module if titanoboa is absent

from venom_opt import balance_patch as bp  # noqa: E402
from venom_opt.erc20_abi import (  # noqa: E402
    ALLOWANCE,
    APPROVE,
    BALANCE_OF,
    MINT,
    TOTAL_SUPPLY,
    TRANSFER,
    TRANSFER_FROM,
    arg_addr,
    word,
)

ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "erc20.json"
ONE = 10**18

# Distinct holder / spender addresses used across the parity tests.
A = (0xAA).to_bytes(20, "big")
B = (0xBB).to_bytes(20, "big")
C = (0xCC).to_bytes(20, "big")
D = (0xDD).to_bytes(20, "big")


@pytest.fixture
def instances():
    """A fresh (original, patched) pair of deployed ERC-20 runtimes."""
    creation = bp.creation_from_artifact(ARTIFACT)
    runtime = bp.runtime_from_artifact(ARTIFACT)
    slot = bp.balance_slot_from_artifact(ARTIFACT)
    patched = bp.patch(runtime, slot)
    orig, _ = boa.env.deploy_code(bytecode=creation)
    opt, _ = boa.env.deploy_code(bytecode=creation)
    boa.env.set_code(opt, patched)
    return orig, opt


# ----- observation / call helpers --------------------------------------------

def _call(c, data: bytes, sender: bytes | None = None):
    return boa.env.raw_call(c, sender=sender, data=data)


def _bal(c, who: bytes) -> int:
    return int.from_bytes(_call(c, BALANCE_OF + arg_addr(who)).output, "big")


def _allow(c, owner: bytes, spender: bytes) -> int:
    return int.from_bytes(_call(c, ALLOWANCE + arg_addr(owner) + arg_addr(spender)).output, "big")


def _supply(c) -> int:
    return int.from_bytes(_call(c, TOTAL_SUPPLY).output, "big")


def _reverts(c, data: bytes, sender: bytes | None = None) -> bool:
    """True iff the call reverts (any failure) — for revert-parity assertions."""
    try:
        _call(c, data, sender=sender)
        return False
    except Exception:
        return True


# ----- success-path parity ----------------------------------------------------

def test_mint_transfer_parity(instances):
    """mint to A, transfer A->B: identical balances on both runtimes."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(5 * ONE))
        _call(c, TRANSFER + arg_addr(B) + word(2 * ONE), sender=A)  # transfer FROM A
    assert _bal(orig, A) == _bal(opt, A) == 3 * ONE
    assert _bal(orig, B) == _bal(opt, B) == 2 * ONE
    assert _supply(orig) == _supply(opt) == 5 * ONE


def test_approve_transferfrom_parity(instances):
    """approve + transferFrom exercises the UNpatched allowance path (slot 7);
    balances, allowance and supply must stay identical between orig and opt."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(5 * ONE))
        _call(c, APPROVE + arg_addr(C) + word(3 * ONE), sender=A)  # A approves C
        _call(c, TRANSFER_FROM + arg_addr(A) + arg_addr(B) + word(2 * ONE), sender=C)
    assert _bal(orig, A) == _bal(opt, A) == 3 * ONE
    assert _bal(orig, B) == _bal(opt, B) == 2 * ONE
    assert _allow(orig, A, C) == _allow(opt, A, C) == 1 * ONE  # allowance consumed
    assert _supply(orig) == _supply(opt) == 5 * ONE


def test_self_transfer_parity(instances):
    """A transfers to itself: balance unchanged, identical on both."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(5 * ONE))
        _call(c, TRANSFER + arg_addr(A) + word(2 * ONE), sender=A)
    assert _bal(orig, A) == _bal(opt, A) == 5 * ONE


def test_zero_value_transfer_parity(instances):
    """A zero-value transfer succeeds and moves nothing, identically."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(5 * ONE))
        _call(c, TRANSFER + arg_addr(B) + word(0), sender=A)
    assert _bal(orig, A) == _bal(opt, A) == 5 * ONE
    assert _bal(orig, B) == _bal(opt, B) == 0


def test_full_balance_map_parity(instances):
    """A scripted sequence of mints/transfers across four holders; the FULL
    balance map + totalSupply must match holder-for-holder between orig & opt."""
    orig, opt = instances
    holders = (A, B, C, D)
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(10 * ONE))
        _call(c, MINT + arg_addr(B) + word(7 * ONE))
        _call(c, TRANSFER + arg_addr(C) + word(4 * ONE), sender=A)
        _call(c, TRANSFER + arg_addr(D) + word(3 * ONE), sender=B)
        _call(c, TRANSFER + arg_addr(A) + word(1 * ONE), sender=C)
    for who in holders:
        assert _bal(orig, who) == _bal(opt, who), f"balance mismatch for {who.hex()}"
    assert _supply(orig) == _supply(opt) == 17 * ONE


# ----- failure-path (revert) parity ------------------------------------------

def test_insufficient_balance_reverts_identically(instances):
    """Transferring more than the balance reverts on BOTH runtimes (safe-math
    underflow) — failure parity, not just success parity."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(1 * ONE))
    data = TRANSFER + arg_addr(B) + word(2 * ONE)
    assert _reverts(orig, data, sender=A) is True
    assert _reverts(opt, data, sender=A) is True
    # and nothing moved on either
    assert _bal(orig, A) == _bal(opt, A) == 1 * ONE
    assert _bal(orig, B) == _bal(opt, B) == 0


def test_transferfrom_without_allowance_reverts_identically(instances):
    """transferFrom with no allowance reverts identically (allowance underflow)."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(5 * ONE))
    data = TRANSFER_FROM + arg_addr(A) + arg_addr(B) + word(1 * ONE)
    assert _reverts(orig, data, sender=C) is True
    assert _reverts(opt, data, sender=C) is True
    assert _bal(orig, A) == _bal(opt, A) == 5 * ONE


# ----- the point of the peephole ---------------------------------------------

def test_gas_not_worse(instances):
    """The peephole never costs more — it drops a KECCAK256 per balance access."""
    orig, opt = instances
    for c in (orig, opt):  # warm the slots first
        _call(c, MINT + arg_addr(A) + word(ONE))
    gO = _call(orig, MINT + arg_addr(A) + word(ONE)).get_gas_used()
    gP = _call(opt, MINT + arg_addr(A) + word(ONE)).get_gas_used()
    assert gP <= gO
