"""General ABI v2 encoder — static *and* dynamic (bytes / arrays / tuples).

:mod:`venom_opt.erc20_abi` only encodes primitive words (``word`` / ``arg_addr``),
which cannot drive functions taking ``Bytes[..]`` / ``DynArray[..]`` / struct
arguments — so the differential harness could not exercise dynamic-argument
entry points at all. This module adds the full head/tail encoding of the
Solidity/Vyper ABI, small enough to audit by eye.

A value is encoded as an :data:`Enc` pair ``(is_dynamic, payload)``:

* static values inline their payload in the head area;
* dynamic values contribute a 32-byte offset word to the head area and append
  their payload to the tail area (offsets are relative to the enclosing frame).

The layout reference is the *verified* encoder in the sibling evm-abi-lean
development (``encodeArgs``, roundtrip-proven by ``roundtrip_args``,
nested tuples/structs included). The harness stays Python-only — the same
decoupling philosophy as the EVMYulLean mapping guard — but the unit tests
cross-check byte-for-byte against ``eth_abi``.
"""

from __future__ import annotations

from venom_opt.erc20_abi import arg_addr, word

#: (is_dynamic, payload) — the unit the head/tail combinator operates on.
Enc = tuple[bool, bytes]


def _pad_right(b: bytes) -> bytes:
    """Right-pad to the next 32-byte boundary (dynamic payload padding)."""
    return b + b"\x00" * (-len(b) % 32)


# ----- leaf encoders -----------------------------------------------------------


def enc_uint(x: int) -> Enc:
    """uint256 (or any uintN passed as an int)."""
    return (False, word(x))


def enc_bool(x: bool) -> Enc:
    return (False, word(1 if x else 0))


def enc_address(x: int | bytes) -> Enc:
    return (False, arg_addr(x))


def enc_bytes(b: bytes) -> Enc:
    """Dynamic ``bytes``: length word + right-padded data."""
    return (True, word(len(b)) + _pad_right(bytes(b)))


def enc_string(s: str) -> Enc:
    return enc_bytes(s.encode())


# ----- containers --------------------------------------------------------------


def enc_tuple(*items: Enc) -> Enc:
    """Tuple / struct: static iff every field is static (then it inlines in the
    head area); dynamic as soon as one field is dynamic."""
    return (any(dyn for dyn, _ in items), _head_tail(list(items)))


def enc_fixed_array(items: list[Enc]) -> Enc:
    """Fixed-size array ``T[n]``: no length prefix, same head/tail as a tuple
    of ``n`` same-typed fields."""
    return (any(dyn for dyn, _ in items), _head_tail(items))


def enc_dyn_array(items: list[Enc]) -> Enc:
    """Dynamic array ``T[]`` / ``DynArray[T, n]``: length word + head/tail of
    the elements. Always dynamic."""
    return (True, word(len(items)) + _head_tail(items))


# ----- the head/tail combinator -------------------------------------------------


def _head_tail(items: list[Enc]) -> bytes:
    """ABI head/tail assembly for one frame: static payloads inline in the head,
    dynamic payloads go to the tail with a head offset word pointing at them
    (offset measured from the start of this frame's head area)."""
    head_size = sum(32 if dyn else len(payload) for dyn, payload in items)
    heads = b""
    tails = b""
    for dyn, payload in items:
        if dyn:
            heads += word(head_size + len(tails))
            tails += payload
        else:
            heads += payload
    return heads + tails


# ----- call-data assembly --------------------------------------------------------


def encode_args(*items: Enc) -> bytes:
    """ABI-encode a function argument list (the top-level frame)."""
    return _head_tail(list(items))


def encode_call(selector: bytes, *items: Enc) -> bytes:
    """4-byte selector + encoded arguments — ready for ``raw_call``."""
    return selector + encode_args(*items)
