"""Differential test: the patched runtime behaves identically to the original.

Deploys the ERC-20 twice via its creation code, etches the peephole-patched
runtime onto the second instance, then exercises mint / transfer / balanceOf and
asserts behavioural parity (plus reports the gas delta — the point of the
peephole). Skipped automatically if titanoboa is not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

boa = pytest.importorskip("boa")  # skip the whole module if titanoboa is absent

from venom_opt import balance_patch as bp  # noqa: E402
from venom_opt.erc20_abi import BALANCE_OF, MINT, TRANSFER, arg_addr, word  # noqa: E402

ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "erc20.json"
ONE = 10**18


@pytest.fixture
def instances():
    creation = bp.creation_from_artifact(ARTIFACT)
    runtime = bp.runtime_from_artifact(ARTIFACT)
    slot = bp.balance_slot_from_artifact(ARTIFACT)
    patched = bp.patch(runtime, slot)
    orig, _ = boa.env.deploy_code(bytecode=creation)
    opt, _ = boa.env.deploy_code(bytecode=creation)
    boa.env.set_code(opt, patched)
    return orig, opt


def _bal(c, who: bytes) -> int:
    out = boa.env.raw_call(c, data=BALANCE_OF + arg_addr(who)).output
    return int.from_bytes(out, "big")


def test_mint_transfer_parity(instances):
    orig, opt = instances
    A, B = (0xAA).to_bytes(20, "big"), (0xBB).to_bytes(20, "big")
    for c in (orig, opt):
        boa.env.raw_call(c, data=MINT + arg_addr(A) + word(5 * ONE))
        boa.env.raw_call(c, data=TRANSFER + arg_addr(B) + word(2 * ONE))
    # identical balances on both runtimes
    assert _bal(orig, A) == _bal(opt, A) == 3 * ONE
    assert _bal(orig, B) == _bal(opt, B) == 2 * ONE


def test_gas_not_worse(instances):
    orig, opt = instances
    A = (0xAA).to_bytes(20, "big")
    for c in (orig, opt):  # warm the slots first
        boa.env.raw_call(c, data=MINT + arg_addr(A) + word(ONE))
    gO = boa.env.raw_call(orig, data=MINT + arg_addr(A) + word(ONE)).get_gas_used()
    gP = boa.env.raw_call(opt, data=MINT + arg_addr(A) + word(ONE)).get_gas_used()
    assert gP <= gO  # the peephole never costs more (drops a KECCAK256)
