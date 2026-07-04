#!/usr/bin/env python3
"""Venom balance-slot peephole patcher.

Rewrites every Venom-emitted ``self.balanceOf[key]`` keccak slot derivation
into ``~key`` — length-preserving — by tracking *constant memory words* along
the runtime bytecode. Venom derives a mapping slot as ``keccak256(mem[o ..
o+0x40))`` with the map's slot stored at the frame base ``o`` and the key at
``o+0x20``; straight-line code uses ``o = 0``, but loop bodies (e.g. a
``DynArray`` batch transfer) place the same idiom at other constant offsets
(``0x20``, ``0x60``, ...). The patcher recognizes the keccak *tail* wherever
the tracked frame base holds the balance slot:

* ``60 40 5f 20``    (PUSH1 0x40 PUSH0 KECCAK256, frame at 0)
    -> ``60 20 51 19``       (PUSH1 0x20 MLOAD NOT)
* ``60 40 60 o 20``  (PUSH1 0x40 PUSH1 o KECCAK256, frame at ``o``)
    -> ``61 00 (o+20) 51 19`` (PUSH2 o+0x20 MLOAD NOT)

Both replacements are byte-length- and stack-effect-preserving, and read back
exactly the key word the keccak would have hashed — whatever instruction put
it there — so the frame's key store needs no pattern of its own.

A keccak is a balance access iff the tracked ``mem[o] == balance_slot``,
which excludes allowance (the nested map), nonces, etc. The rewrite is *sound*
because ``~`` (bitwise-not) is injective, so distinct keys keep distinct
slots — there is no aliasing. That soundness is machine-checked Lean in
``verification/`` (``VenomOpt.Peephole``) and, end-to-end against the real
EVM semantics, in the EVMYulLean development (``venomBalanceLoad_orig_opt_equiv``).

The tracked words are a linear-scan approximation, so the tracking must be
*conservative*: a constant store overwrites the word it hits and forgets any
tracked word it overlaps; anything that writes memory where the scan cannot
tell — a computed-offset ``MSTORE``/``MSTORE8``, the copy family
(``CALLDATACOPY`` / ``CODECOPY`` / ``EXTCODECOPY`` / ``RETURNDATACOPY`` /
``MCOPY``), a call (which writes return data) — wipes the whole map. So does
``JUMPDEST``: it is a basic-block boundary, and a jump may land there with
different memory than the linearly-preceding bytes established. Unknown never
matches ``balance_slot``, so imprecision can only *miss* sites (caught by the
site-count check and the differential tests), never rewrite a wrong one.

**Beyond address keys.** The pattern is keyed by the *slot* at the frame base,
so ``patch(code, slot)`` optimizes whichever map you point it at:

* single-word keys (``address``, ``bytes32``, ``uintN``) — the frame's key IS
  the key; ``~key`` is injective outright;
* dynamic keys (``String[..]``/``Bytes[..]``) — Venom derives the slot in two
  stages: an inner *variable-size* keccak of the key bytes (never matched by
  the patterns above), then the same 64-byte outer keccak over
  ``slot ++ innerHash``. Patching rewrites only the outer stage to
  ``~innerHash``, dropping one KECCAK256 per access; inner-hash collisions
  collide in the ORIGINAL derivation too, so the rewrite is equivalence-
  preserving relative to the original with no new assumptions.

**ONE optimized map per contract.** ``~key`` erases the slot input, so
optimizing two maps in the same contract aliases them *deterministically*
whenever their key words coincide (e.g. ``balanceOf[a]`` and
``tags[bytes32(a)]``) — not a hash-collision risk, a certainty. ``patch``
takes a single slot by design; do not feed its output back to optimize a
second map.

``patch`` operates on **runtime** bytecode (to ``etch``/``set_code`` in a
simulated EVM). For a real broadcast you cannot etch, so use ``patch_creation``,
which patches the runtime *embedded inside the creation code* (length-preserving,
so a normal deploy runs the constructor and returns the optimized runtime).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: Default balanceOf slot. Resolve the real one per contract with
#: :func:`balance_slot_from_artifact` — layouts differ (this repo's ERC20 puts
#: balanceOf at slot 6; the Snekmate demo put it at 2).
BALANCE_SLOT = 0x02

#: Opcodes that write memory at a stack-computed destination — any of them may
#: clobber a tracked word, so the tracker must forget everything it knew.
_MEM_CLOBBERS = frozenset(
    {
        0x52,  # MSTORE (computed offset; constant-offset forms are decoded below)
        0x53,  # MSTORE8
        0x37,  # CALLDATACOPY
        0x39,  # CODECOPY
        0x3C,  # EXTCODECOPY
        0x3E,  # RETURNDATACOPY
        0x5E,  # MCOPY
        0xF1,  # CALL        (writes return data)
        0xF2,  # CALLCODE
        0xF4,  # DELEGATECALL
        0xFA,  # STATICCALL
    }
)


def _forget_overlap(mem: dict[int, int], off: int, width: int = 32) -> None:
    """Drop every tracked word the write ``[off, off+width)`` can touch."""
    for o in [o for o in mem if o < off + width and o + 32 > off]:
        del mem[o]


def _const_push(code: bytearray, i: int) -> tuple[int, int] | None:
    """Decode a constant push at ``i``: PUSH0 -> ``(0, 1)``, PUSHn imm ->
    ``(imm, 1+n)``. Returns ``(value, byte length)``, or None if ``i`` is not a
    complete push."""
    if i >= len(code):
        return None
    op = code[i]
    if op == 0x5F:
        return 0, 1
    if 0x60 <= op <= 0x7F and (j := i + 1 + (op - 0x5F)) <= len(code):
        return int.from_bytes(code[i + 1 : j], "big"), j - i
    return None


def _walk(code: bytearray, do_patch: bool, bal_slot: int) -> int:
    """One walk, shared by counting and patching. Returns the number of balance
    keccak sites seen (pre-patch == expected, post-patch == 0)."""
    i, n, count = 0, len(code), 0
    mem: dict[int, int] = {}  # tracked constant memory words {offset: value}
    while i < n:
        op = code[i]

        # (1) keccak over a constant 64-byte frame whose base holds the balance
        # slot: PUSH1 0x40 (PUSH0 | PUSH1 o) KECCAK256. The frame is
        # slot ++ key, so the slot derivation is replaced by ~key — an MLOAD of
        # the very word the keccak would have hashed at o+0x20. Replacements
        # preserve byte length and stack effect.
        if op == 0x60 and i + 4 <= n and code[i + 1] == 0x40:
            site = None  # (frame offset, replacement)
            if code[i + 2] == 0x5F and code[i + 3] == 0x20:
                site = (0, bytes([0x60, 0x20, 0x51, 0x19]))  # PUSH1 0x20 MLOAD NOT
            elif code[i + 2] == 0x60 and i + 5 <= n and code[i + 4] == 0x20:
                o = code[i + 3]
                site = (
                    o,
                    bytes([0x61, *divmod(o + 0x20, 256), 0x51, 0x19]),
                )  # PUSH2 o+0x20 MLOAD NOT
            if site is not None:
                o, repl = site
                if mem.get(o) == bal_slot:
                    count += 1
                    if do_patch:
                        code[i : i + len(repl)] = repl
                i += len(repl)
                continue

        # (2) constant value stored to a constant offset — track the word:
        # (PUSH0 | PUSHn <v>) (PUSH0 | PUSHn <c>) MSTORE
        if (v := _const_push(code, i)) is not None:
            c = _const_push(code, i + v[1])
            if c is not None and (j := i + v[1] + c[1]) < n and code[j] == 0x52:
                _forget_overlap(mem, c[0])
                mem[c[0]] = v[0]
                i = j + 1
                continue

        # (3) unknown value stored to a constant offset — forget what it hits:
        # <value> (PUSH0 | PUSHn <c>) MSTORE|MSTORE8
        if (c := _const_push(code, i)) is not None:
            if (j := i + c[1]) < n and code[j] in (0x52, 0x53):
                _forget_overlap(mem, c[0], 32 if code[j] == 0x52 else 1)
                i = j + 1
                continue
            i += c[1]  # ordinary push: skip opcode + immediate
            continue

        # (4) memory write at a computed destination: forget everything
        if op in _MEM_CLOBBERS:
            mem.clear()
            i += 1
            continue

        # (5) JUMPDEST: basic-block boundary — the linear predecessor is not
        # necessarily the dynamic predecessor, so the tracked state must die.
        if op == 0x5B:
            mem.clear()
            i += 1
            continue

        i += 1
    return count


def count_sites(code: bytes, bal_slot: int = BALANCE_SLOT) -> int:
    """How many balanceOf keccak derivations the patcher would rewrite."""
    return _walk(bytearray(code), False, bal_slot)


def patch(code: bytes, bal_slot: int = BALANCE_SLOT) -> bytes:
    """Return a patched copy of ``code`` (same length). Raises if no site is
    found (a wrong ``bal_slot`` / layout would silently no-op otherwise)."""
    b = bytearray(code)
    n = _walk(b, True, bal_slot)
    if n == 0:
        raise ValueError(f"no balance sites for slot 0x{bal_slot:02x}; wrong layout?")
    if len(b) != len(code):
        raise AssertionError("length not preserved")
    return bytes(b)


def patch_creation(creation: bytes, runtime: bytes, bal_slot: int = BALANCE_SLOT) -> bytes:
    """Patch the runtime *embedded* in ``creation`` so a normal deploy returns
    the optimized runtime while still running the constructor — broadcastable
    init code (unlike ``patch`` + etch, which is simulation only)."""
    occ = creation.count(runtime)
    if occ != 1:
        raise ValueError(
            f"runtime appears {occ}x in creation (expected exactly 1); cannot patch the embedded runtime unambiguously"
        )
    patched_runtime = patch(runtime, bal_slot)
    out = creation.replace(runtime, patched_runtime)
    if len(out) != len(creation):
        raise AssertionError("length not preserved")
    return out


# --- artifact helpers --------------------------------------------------------


def _hex_to_bytes(h: str) -> bytes:
    h = h.strip()
    return bytes.fromhex(h[2:] if h.startswith("0x") else h)


def runtime_from_artifact(path: str | Path) -> bytes:
    art = json.loads(Path(path).read_text())
    return _hex_to_bytes(art["deployedBytecode"]["object"])


def creation_from_artifact(path: str | Path) -> bytes:
    art = json.loads(Path(path).read_text())
    return _hex_to_bytes(art["bytecode"]["object"])


def balance_slot_from_artifact(path: str | Path) -> int:
    """Read balanceOf's storage slot from the artifact's ``storageLayout`` —
    the correct ``bal_slot`` for this exact contract, no guessing."""
    art = json.loads(Path(path).read_text())
    return int(art["storageLayout"]["balanceOf"]["slot"])


#: Map VALUE types occupying exactly one storage word — the only values the
#: length-preserving ``~key`` rewrite is sound for. A multi-word value (struct,
#: ``String[..]``/``Bytes[..]``, fixed array) lives at keccak-base+i; ``~key``
#: packs bases densely (``~(k+1) = ~k - 1``), so adjacent keys' value windows
#: would interleave — deterministic corruption, demonstrated by an
#: out-of-gas explosion on the first write to a patched
#: ``HashMap[address, String[64]]``. (The sound multi-word layout,
#: ``strideSlot``, is proved in EVMYulLean's SlotPacking.lean and needs the
#: future IR-level pass.)
_WORD_VALUE = re.compile(r"^(u?int\d+|address|bool|decimal|bytes([12]?\d|3[0-2]))$")


def _hashmap_value_type(type_str: str) -> str | None:
    """The VALUE type of a ``HashMap[key, value]`` type string (top-level
    comma split), or None if it isn't a HashMap."""
    m = re.fullmatch(r"HashMap\[(.*)\]", type_str.strip())
    if not m:
        return None
    inner, depth, split = m.group(1), 0, None
    for i, ch in enumerate(inner):
        depth += ch in "[("
        depth -= ch in "])"
        if ch == "," and depth == 0:
            split = i
            break
    return inner[split + 1 :].strip() if split is not None else None


def map_slot_from_artifact(path: str | Path, name: str) -> int:
    """Slot of map ``name`` from the artifact's ``storageLayout``, refusing
    maps whose VALUE spans more than one storage word (see ``_WORD_VALUE``)."""
    art = json.loads(Path(path).read_text())
    entry = art["storageLayout"][name]
    value_type = _hashmap_value_type(entry.get("type", ""))
    if value_type is None:
        raise ValueError(f"{name} is not a HashMap (type: {entry.get('type')!r})")
    if not _WORD_VALUE.match(value_type):
        raise ValueError(
            f"refusing to optimize {name}: its value type {value_type!r} spans "
            "multiple storage words, and the length-preserving ~key rewrite "
            "would interleave adjacent keys' value windows (see the module "
            "docstring). Only single-word values are patchable."
        )
    return int(entry["slot"])
