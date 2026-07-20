"""The IR-level pass: multi-map optimization the bytecode patcher cannot do.

:mod:`venom_opt.ir_pass` rewrites balance-slot ``sha3`` derivations in Venom IR
(before codegen, so length is unconstrained) into the verified ``packSlot``
scheme — ``~(id*2^160 + key)``. That lets SEVERAL address-keyed maps be
optimized in one contract with distinct ids, provably non-aliasing
(``packSlot_injective`` / ``packSlot_cross_map`` in EVMYulLean's
SlotPacking.lean). Plain ``~key`` (id 0 for every map) cannot: two maps alias
at any shared key (``two_fullword_maps_must_alias``).

Skipped automatically if titanoboa is not installed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from venom_opt import ir_pass

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
TWOMAPS = CONTRACTS / "TwoMaps.vy"
ERC20 = CONTRACTS / "ERC20.vy"


def _slots(contract: Path) -> dict[str, int]:
    layout = json.loads(
        subprocess.run(
            ["vyper", "-f", "layout", str(contract)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )["storage_layout"]
    return {k: v["slot"] for k, v in layout.items()}


# ----- pass-level: rewrite counts + IR/compile round trip ----------------------


def test_single_map_rewrites_and_compiles():
    src = ir_pass.runtime_ir_from_contract(ERC20)
    before = src.count("sha3")
    out, n = ir_pass.optimize_ir(src, {6: 0})  # balanceOf slot 6, id 0 = ~key
    assert n == 6
    assert out.count("sha3") == before - 6  # only balanceOf's, allowance's remain
    assert len(ir_pass.compile_ir(out)) > 0


def test_multi_map_rewrites_both():
    src = ir_pass.runtime_ir_from_contract(TWOMAPS)
    slots = _slots(TWOMAPS)
    out, n = ir_pass.optimize_ir(src, {slots["balances"]: 1, slots["bonuses"]: 2})
    assert n == 6  # 3 accesses per map (set + add-load + add-store)
    assert out.count("sha3") == 0  # both maps de-keccaked


boa = pytest.importorskip("boa")

from venom_opt.erc20_abi import arg_addr, selector, word  # noqa: E402

STUB = bytes.fromhex("5f5ff3")  # PUSH0 PUSH0 RETURN — returns empty runtime


def _etch(runtime: bytes):
    addr, _ = boa.env.deploy_code(bytecode=STUB)
    boa.env.set_code(addr, runtime)
    return addr


def _get(c, sig: str, key: bytes) -> int:
    return int.from_bytes(boa.env.raw_call(c, data=selector(sig) + arg_addr(key)).output, "big")


def _call(c, sig: str, key: bytes, val: int):
    boa.env.raw_call(c, data=selector(sig) + arg_addr(key) + word(val))


# ----- the decisive case: two maps, one contract, distinct ids ------------------


def test_two_maps_distinct_ids_no_alias():
    """balances (id 1) and bonuses (id 2) probed at the SAME key never
    interfere — packSlot_injective / cross_map, the case ~key cannot do."""
    src = ir_pass.runtime_ir_from_contract(TWOMAPS)
    slots = _slots(TWOMAPS)
    orig = _etch(ir_pass.compile_ir(src))
    opt = _etch(ir_pass.compile_ir(ir_pass.optimize_ir(src, {slots["balances"]: 1, slots["bonuses"]: 2})[0]))
    key = (0x1234).to_bytes(20, "big")
    for c in (orig, opt):
        _call(c, "set_balance(address,uint256)", key, 100)
        _call(c, "set_bonus(address,uint256)", key, 7)
        _call(c, "add_both(address,uint256)", key, 5)
    assert _get(orig, "balances(address)", key) == _get(opt, "balances(address)", key) == 105
    assert _get(orig, "bonuses(address)", key) == _get(opt, "bonuses(address)", key) == 12


def test_two_maps_same_id_zero_aliases():
    """The impossibility, empirically: both maps at id 0 (~key) alias at any
    shared key — this is why distinct ids (packSlot) are required."""
    src = ir_pass.runtime_ir_from_contract(TWOMAPS)
    slots = _slots(TWOMAPS)
    bad = _etch(ir_pass.compile_ir(ir_pass.optimize_ir(src, {slots["balances"]: 0, slots["bonuses"]: 0})[0]))
    key = (0x1234).to_bytes(20, "big")
    _call(bad, "set_balance(address,uint256)", key, 100)
    _call(bad, "set_bonus(address,uint256)", key, 7)
    # balances[key] reads back the bonus value — deterministic collision
    assert _get(bad, "balances(address)", key) == _get(bad, "bonuses(address)", key) == 7


def test_two_maps_distinct_keys_independent():
    """Distinct keys in the two optimized maps stay independent."""
    src = ir_pass.runtime_ir_from_contract(TWOMAPS)
    slots = _slots(TWOMAPS)
    opt = _etch(ir_pass.compile_ir(ir_pass.optimize_ir(src, {slots["balances"]: 1, slots["bonuses"]: 2})[0]))
    ka, kb = (0xAA).to_bytes(20, "big"), (0xBB).to_bytes(20, "big")
    _call(opt, "set_balance(address,uint256)", ka, 11)
    _call(opt, "set_bonus(address,uint256)", kb, 22)
    assert _get(opt, "balances(address)", ka) == 11
    assert _get(opt, "bonuses(address)", kb) == 22
    assert _get(opt, "balances(address)", kb) == 0
    assert _get(opt, "bonuses(address)", ka) == 0


def test_single_map_ir_matches_original_behaviour():
    """The IR pass on one map (id 0) is behaviourally identical to the
    unoptimized contract — a full ERC20-style exercise."""
    src = ir_pass.runtime_ir_from_contract(TWOMAPS)
    slots = _slots(TWOMAPS)
    orig = _etch(ir_pass.compile_ir(src))
    opt = _etch(ir_pass.compile_ir(ir_pass.optimize_ir(src, {slots["balances"]: 0})[0]))
    key = (0x99).to_bytes(20, "big")
    for c in (orig, opt):
        _call(c, "set_balance(address,uint256)", key, 50)
        _call(c, "add_both(address,uint256)", key, 8)
    assert _get(orig, "balances(address)", key) == _get(opt, "balances(address)", key) == 58
    # bonuses (unoptimized here) still agrees
    assert _get(orig, "bonuses(address)", key) == _get(opt, "bonuses(address)", key) == 8


# ----- Phase 3: multi-word VALUE maps via strideSlot -----------------------------
#
# A String/struct-valued map lives at keccak-base+i. The pass rewrites the base
# to ~(key*stride) and flips each `add base, off` field access to `sub base,
# off` = ~(key*stride + off) = strideSlot -- windows abut instead of
# interleaving (the case the length-preserving ~key rewrite explodes on).

DYNVALUE = CONTRACTS / "DynValue.vy"


def _dynvalue_ir_pair(mwmap="notes", pack=None):
    src = ir_pass.runtime_ir_from_contract(DYNVALUE)
    opt = src
    if pack is not None:
        opt = ir_pass.optimize_ir(opt, pack)[0]
    opt = ir_pass.optimize_ir_multiword(opt, _slots(DYNVALUE)[mwmap])[0]
    return _etch(ir_pass.compile_ir(src)), _etch(ir_pass.compile_ir(opt))


def _set_note(c, key: bytes, note: str):
    from venom_opt.abi import enc_address, enc_string, encode_call

    boa.env.raw_call(c, data=encode_call(selector("set_note(address,string)"), enc_address(key), enc_string(note)))


def _note(c, key: bytes) -> bytes:
    return bytes(boa.env.raw_call(c, data=selector("notes(address)") + arg_addr(key)).output)


def test_strideslot_adjacent_keys_no_corruption():
    """The case the ~key rewrite explodes on: multi-word values at ADJACENT
    keys. strideSlot keeps their windows disjoint."""
    orig, opt = _dynvalue_ir_pair()
    a, a1 = (0x1000).to_bytes(20, "big"), (0x1001).to_bytes(20, "big")  # ~a and ~(a+1) adjacent
    note_a, note_b = "alice-" + "x" * 54, "bob-" + "y" * 56  # 60 chars: 3 words each
    for c in (orig, opt):
        _set_note(c, a, note_a)
        _set_note(c, a1, note_b)
    assert _note(orig, a) == _note(opt, a)
    assert _note(orig, a1) == _note(opt, a1)
    assert note_a.encode() in _note(opt, a)
    assert note_b.encode() in _note(opt, a1)  # not clobbered by the adjacent key


def test_strideslot_coexists_with_packslot_balance():
    """balanceOf (packSlot) and notes (strideSlot) optimized in one contract."""
    orig, opt = _dynvalue_ir_pair(mwmap="notes", pack={0: 0})  # balanceOf slot 0, id 0
    a = (0xAB).to_bytes(20, "big")
    for c in (orig, opt):
        _set_note(c, a, "hello world " * 4)
        boa.env.raw_call(c, data=selector("mint(address,uint256)") + arg_addr(a) + word(99))
    assert _note(orig, a) == _note(opt, a)
    assert bytes(boa.env.raw_call(orig, data=selector("balanceOf(address)") + arg_addr(a)).output) == bytes(
        boa.env.raw_call(opt, data=selector("balanceOf(address)") + arg_addr(a)).output
    )


def test_strideslot_rewrites_base_count():
    src = ir_pass.runtime_ir_from_contract(DYNVALUE)
    before = src.count("sha3")
    out, n = ir_pass.optimize_ir_multiword(src, _slots(DYNVALUE)["notes"])
    assert n == 2  # set_note + the notes getter
    assert out.count("sha3") == before - 2  # only balanceOf's remain
    assert out.count("= sub ") >= 2  # field accesses flipped add -> sub
