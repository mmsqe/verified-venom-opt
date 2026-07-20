"""Venom **IR-level** balance-slot optimization — the pre-deployment pass.

The bytecode patcher (:mod:`venom_opt.balance_patch`) is length-preserving, so
it can only install `~key` (a 4-byte tail) and thus optimizes ONE map per
contract with single-word values. This pass works on the Venom IR *before*
codegen, where length is irrelevant, so it can install the stronger verified
slot schemes from EVMYulLean's ``SlotPacking.lean``:

* **map-id packing** (``packSlot id key = ~(id·2^160 + key)``) — several
  address-keyed maps optimized in one contract, provably non-aliasing
  (``packSlot_injective`` / ``packSlot_cross_map``);
* map id 0 is exactly ``~key``, so a single map reproduces the bytecode scheme.

A Vyper mapping slot derivation in Venom IR is

    mstore <off0>, <slot>      ; frame base
    mstore <off1>, <key>       ; off1 = off0 + 32
    %h = sha3 <off0>, <64>

This pass rewrites the `sha3` (and its two staging `mstore`s, now dead) to

    %t  = add <key>, <id·2^160>    ; omitted when id = 0
    %h  = not %t

replacing a KECCAK256 with one/two cheap ops. Values are resolved by following
Venom's SSA `assign` chains to a literal (constants are materialized into vars),
so the pass sees the *constant* frame base a `sha3` hashes.

Compile the rewritten IR to bytecode with the standalone ``venom`` CLI
(``compile_ir``). The value-type guard (``balance_patch.map_slot_from_artifact``)
still applies per map — multi-word values need ``strideSlot`` (a later pass).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from vyper.venom.parser import parse_venom

#: map-id stride: each map's key window is `id·2^160`, leaving 160 bits for an
#: address key. Mirrors EVMYulLean `packSlot`'s 2^160 (and `strideSlot` at
#: stride 2^160).
_ID_STRIDE = 2**160

#: per-key value window for MULTI-WORD values (`strideSlot`): 2^16 words is far
#: larger than any Vyper value yet well within EVMYulLean's proved bounds
#: (`strideSlot_above_named_slot` needs stride ≤ 2^32, `strideSlot_injective`
#: needs the field offset < stride). A value's field `off` lands at
#: `strideSlot stride key off = ~(key·stride + off) = ~(key·stride) − off`.
_VALUE_STRIDE = 2**16


def _resolve_const(defs: dict, var) -> int | None:
    """Follow `assign` chains from `var` to a literal, or None if not constant."""
    from vyper.venom.basicblock import IRLiteral, IRVariable

    seen = set()
    cur = var
    while isinstance(cur, IRVariable):
        name = cur.name
        if name in seen or name not in defs:
            return None
        seen.add(name)
        inst = defs[name]
        if inst.opcode != "assign":
            return None
        cur = inst.operands[0]
    return cur.value if isinstance(cur, IRLiteral) else None


def _balance_sha3_sites(bb, defs: dict, want_slot) -> list:
    """Collect the mapping-slot `sha3`s in `bb` whose constant frame base slot
    satisfies `want_slot(slot)`. Tracks the constant word at each memory offset
    across the block's `mstore`s (so the sha3's base offset resolves to a slot),
    and finds the key staged at `base+32` most recently PRECEDING the sha3 (a
    block may hold several accesses). Returns `[(sha3_inst, slot, key_op)]`."""
    mem: dict[int, int] = {}
    sites = []
    for inst in bb.instructions:
        if inst.opcode == "mstore":
            val, off = inst.operands  # Venom `mstore off, val` prints val first
            coff, cval = _resolve_const(defs, off), _resolve_const(defs, val)
            if coff is not None:
                if cval is not None:
                    mem[coff] = cval
                else:
                    mem.pop(coff, None)
        elif inst.opcode == "sha3":
            size, off = inst.operands  # `sha3 off, size`
            coff, csize = _resolve_const(defs, off), _resolve_const(defs, size)
            if csize != 64 or coff is None:
                continue
            slot = mem.get(coff)
            if slot is None or not want_slot(slot):
                continue
            key_inst = _find_mstore(bb, coff + 32, defs, before=bb.instructions.index(inst))
            if key_inst is not None:
                sites.append((inst, slot, key_inst.operands[0]))
    return sites


def _rewrite_block(bb, slot_to_id: dict[int, int], defs: dict) -> int:
    """Rewrite every balance-slot `sha3` in `bb` whose frame base is an
    optimized map's slot into the `packSlot` form. Returns sites rewritten."""
    from vyper.venom.basicblock import IRInstruction, IRLiteral

    sites = _balance_sha3_sites(bb, defs, lambda s: s in slot_to_id)
    for inst, slot, key_op in sites:
        mid = slot_to_id[slot]
        # (%t = add key, id·2^160 ;) %h = not <key|%t>
        new_insts, src = [], key_op
        if mid != 0:
            t = bb.parent.get_next_variable()
            add_inst = IRInstruction("add", [IRLiteral(mid * _ID_STRIDE), key_op], outputs=[t])
            add_inst.parent = bb
            new_insts.append(add_inst)
            src = t
        not_inst = IRInstruction("not", [src], outputs=[inst.output])
        not_inst.parent = bb
        new_insts.append(not_inst)
        idx = bb.instructions.index(inst)
        bb.instructions[idx : idx + 1] = new_insts
    return len(sites)


def _find_mstore(bb, offset: int, defs: dict, before: int):
    """The last `mstore` to constant `offset` in `bb` before index `before`."""
    found = None
    for inst in bb.instructions[:before]:
        if inst.opcode == "mstore":
            _, off = inst.operands
            if _resolve_const(defs, off) == offset:
                found = inst
    return found


def _def_map(fn) -> dict:
    """Variable name -> its defining instruction, across the function."""
    defs = {}
    for bb in fn.get_basic_blocks():
        for inst in bb.instructions:
            if len(inst._outputs) == 1:
                defs[inst.output.name] = inst
    return defs


def optimize_ir(venom_src: str, slot_to_id: dict[int, int]) -> tuple[str, int]:
    """Rewrite the balance-slot derivations of the given map slots in Venom IR
    text. `slot_to_id` maps each optimized map's storage slot to its packing id
    (use `{slot: 0}` for a single map = the `~key` scheme). Returns the
    rewritten IR text and the number of sites rewritten."""
    ctx = parse_venom(venom_src)
    total = 0
    for fn in ctx.functions.values():
        defs = _def_map(fn)
        for bb in fn.get_basic_blocks():
            total += _rewrite_block(bb, slot_to_id, defs)
    return str(ctx), total


def _uses_of(fn, name: str) -> list:
    """Every instruction anywhere in `fn` that reads variable `name`."""
    from vyper.venom.basicblock import IRVariable

    uses = []
    for bb in fn.get_basic_blocks():
        for inst in bb.instructions:
            if any(isinstance(o, IRVariable) and o.name == name for o in inst.operands):
                uses.append((bb, inst))
    return uses


def _is_field_access(base, inst) -> bool:
    """A base slot may only flow into an `add base, off` (field `off`) or a
    direct `sload`/`sstore`/`tload`/`tstore` at the base (field 0). Anything
    else means the base is not a plain multi-word slot and must not be touched."""
    if inst.opcode == "add":
        return True
    if inst.opcode in ("sload", "tload", "sstore", "tstore"):
        return inst.operands[0] is base  # key/slot is the first operand
    return False


def _rewrite_multiword_block(bb, slot: int, stride: int, fn, defs: dict) -> int:
    """Rewrite the multi-word-value base `sha3`s for `slot` in `bb` into
    `~(key·stride)` and flip every downstream `add base, off` field access to
    `sub base, off` (= `~(key·stride + off)` = strideSlot). Returns sites done."""
    from vyper.venom.basicblock import IRInstruction, IRLiteral, IRVariable

    count = 0
    for inst, _slot, key_op in _balance_sha3_sites(bb, defs, lambda s: s == slot):
        base = inst.output
        uses = _uses_of(fn, base.name)
        if not all(_is_field_access(base, i) for _, i in uses):
            continue  # refuse: base flows somewhere other than a field access
        # base := ~(key·stride)  (mul then not, in place of the sha3)
        t = fn.get_next_variable()
        mul_inst = IRInstruction("mul", [IRLiteral(stride), key_op], outputs=[t])
        mul_inst.parent = bb
        not_inst = IRInstruction("not", [t], outputs=[base])
        not_inst.parent = bb
        idx = bb.instructions.index(inst)
        bb.instructions[idx : idx + 1] = [mul_inst, not_inst]
        # field off:  add base, off  ->  sub base, off  (base first)
        for _ubb, uinst in uses:
            if uinst.opcode == "add":
                other = next(o for o in uinst.operands if not (isinstance(o, IRVariable) and o.name == base.name))
                uinst.opcode = "sub"
                uinst.operands = [base, other]
        count += 1
    return count


def optimize_ir_multiword(venom_src: str, slot: int, stride: int = _VALUE_STRIDE) -> tuple[str, int]:
    """Rewrite ONE multi-word-value map (at storage `slot`) to the strideSlot
    scheme — `~(key·stride)` base with `sub`-addressed fields. Returns the
    rewritten IR text and the number of base derivations rewritten. Refuses
    (leaves untouched) any base whose uses are not plain field accesses."""
    ctx = parse_venom(venom_src)
    total = 0
    for fn in ctx.functions.values():
        defs = _def_map(fn)
        for bb in fn.get_basic_blocks():
            total += _rewrite_multiword_block(bb, slot, stride, fn, defs)
    return str(ctx), total


def compile_ir(venom_src: str, evm_version: str | None = None) -> bytes:
    """Compile Venom IR text to runtime bytecode via the standalone `venom` CLI."""
    import tempfile

    args = ["venom"]
    if evm_version:
        args += ["--evm-version", evm_version]
    with tempfile.NamedTemporaryFile("w", suffix=".venom", delete=False) as f:
        f.write(venom_src)
        path = f.name
    try:
        out = subprocess.run([*args, path], check=True, capture_output=True, text=True).stdout
    finally:
        Path(path).unlink()
    hexstr = out.strip()
    return bytes.fromhex(hexstr[2:] if hexstr.startswith("0x") else hexstr)


def runtime_ir_from_contract(contract: str | Path) -> str:
    """Emit a Vyper contract's runtime Venom IR text (`-f ir_runtime`)."""
    out = subprocess.run(
        ["vyper", "--experimental-codegen", "-f", "ir_runtime", str(contract)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out
