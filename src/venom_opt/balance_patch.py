#!/usr/bin/env python3
"""Venom balance-slot peephole patcher.

Rewrites every Venom-emitted ``self.balanceOf[addr]`` keccak slot derivation
into ``~addr`` read back from ``mem[0x20]`` — length-preserving — by tracking
``mem[0x00]`` along the runtime bytecode. Two keccak shapes:

* full  ``60 20 52 60 40 5f 20`` (PUSH1 0x20 MSTORE PUSH1 0x40 PUSH0 KECCAK256)
          -> ``60 20 52 60 20 51 19`` (PUSH1 0x20 MSTORE PUSH1 0x20 MLOAD NOT)
* short ``60 40 5f 20``          (PUSH1 0x40 PUSH0 KECCAK256)
          -> ``60 20 51 19``          (PUSH1 0x20 MLOAD NOT)

A keccak is a balance access iff the tracked ``mem[0x00] == balance_slot``,
which excludes allowance (the nested map), nonces, etc. The rewrite is *sound*
because ``~`` (bitwise-not) is injective, so distinct addresses keep distinct
slots — there is no aliasing. That soundness is machine-checked Lean in
``verification/`` (``VenomOpt.Peephole``) and, end-to-end against the real
EVM semantics, in the EVMYulLean development (``venomBalanceLoad_orig_opt_equiv``).

``patch`` operates on **runtime** bytecode (to ``etch``/``set_code`` in a
simulated EVM). For a real broadcast you cannot etch, so use ``patch_creation``,
which patches the runtime *embedded inside the creation code* (length-preserving,
so a normal deploy runs the constructor and returns the optimized runtime).
"""

from __future__ import annotations

import json
from pathlib import Path

#: Default balanceOf slot. Resolve the real one per contract with
#: :func:`balance_slot_from_artifact` — layouts differ (this repo's ERC20 puts
#: balanceOf at slot 6; the Snekmate demo put it at 2).
BALANCE_SLOT = 0x02


def _walk(code: bytearray, do_patch: bool, bal_slot: int) -> int:
    """One walk, shared by counting and patching. Returns the number of balance
    keccak sites seen (pre-patch == expected, post-patch == 0)."""
    i, n, count = 0, len(code), 0
    mem0 = None  # tracked value of mem[0x00]: an int, or None (unknown)
    while i < n:
        op = code[i]

        # (1) full keccak tail: PUSH1 0x20 MSTORE PUSH1 0x40 PUSH0 KECCAK256
        if (
            op == 0x60
            and i + 7 <= n
            and code[i + 1] == 0x20
            and code[i + 2] == 0x52
            and code[i + 3] == 0x60
            and code[i + 4] == 0x40
            and code[i + 5] == 0x5F
            and code[i + 6] == 0x20
        ):
            if mem0 == bal_slot:
                count += 1
                if do_patch:
                    code[i + 4] = 0x20  # 0x40 -> 0x20
                    code[i + 5] = 0x51  # PUSH0 -> MLOAD
                    code[i + 6] = 0x19  # KECCAK256 -> NOT
            i += 7
            continue

        # (2) short (reuse) keccak: PUSH1 0x40 PUSH0 KECCAK256
        if op == 0x60 and i + 4 <= n and code[i + 1] == 0x40 and code[i + 2] == 0x5F and code[i + 3] == 0x20:
            if mem0 == bal_slot:
                count += 1
                if do_patch:
                    code[i + 1] = 0x20  # 0x40 -> 0x20
                    code[i + 2] = 0x51  # PUSH0 -> MLOAD
                    code[i + 3] = 0x19  # KECCAK256 -> NOT
            i += 4
            continue

        # (3) constant store to mem[0x00]: PUSH1 <v> PUSH0 MSTORE
        if op == 0x60 and i + 4 <= n and code[i + 2] == 0x5F and code[i + 3] == 0x52:
            mem0 = code[i + 1]
            i += 4
            continue

        # (4) zero store to mem[0x00]: PUSH0 PUSH0 MSTORE
        if op == 0x5F and i + 3 <= n and code[i + 1] == 0x5F and code[i + 2] == 0x52:
            mem0 = 0
            i += 3
            continue

        # (5) non-constant store to mem[0x00]: <value> PUSH0 MSTORE
        if op == 0x5F and i + 2 <= n and code[i + 1] == 0x52:
            mem0 = None
            i += 2
            continue

        # (6) ordinary PUSH: skip opcode + immediate bytes
        if 0x60 <= op <= 0x7F:
            i += 1 + (op - 0x5F)
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
            f"runtime appears {occ}x in creation (expected exactly 1); "
            "cannot patch the embedded runtime unambiguously"
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
