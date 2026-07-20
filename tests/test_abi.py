"""Unit tests for the general ABI encoder (:mod:`venom_opt.abi`).

Every encoding is cross-checked byte-for-byte against ``eth_abi`` (the
reference implementation titanoboa already ships), across the static, dynamic,
and nested shapes the differential harness needs. The *verified* reference for
these layouts is evm-abi-lean's ``encodeArgs`` (roundtrip-proven by
``roundtrip_args``); ``eth_abi`` is the executable stand-in here.
"""

from __future__ import annotations

import pytest

eth_abi = pytest.importorskip("eth_abi")

from venom_opt import erc20_abi  # noqa: E402
from venom_opt.abi import (  # noqa: E402
    enc_address,
    enc_bool,
    enc_bytes,
    enc_dyn_array,
    enc_fixed_array,
    enc_string,
    enc_tuple,
    enc_uint,
    encode_args,
    encode_call,
)

A = (0xAA).to_bytes(20, "big")
B = (0xBB).to_bytes(20, "big")


def _addr_str(b: bytes) -> str:
    return "0x" + b.hex()


# ----- static shapes (parity with the legacy erc20_abi encoders) ---------------


def test_transfer_style_matches_eth_abi_and_legacy():
    ours = encode_args(enc_address(A), enc_uint(7))
    ref = eth_abi.encode(["address", "uint256"], [_addr_str(A), 7])
    assert ours == ref
    # and byte-identical to what the existing harness builds by hand
    assert ours == erc20_abi.arg_addr(A) + erc20_abi.word(7)


def test_bool_and_static_tuple():
    ours = encode_args(enc_tuple(enc_address(A), enc_uint(5)), enc_bool(True))
    ref = eth_abi.encode(["(address,uint256)", "bool"], [(_addr_str(A), 5), True])
    assert ours == ref


def test_static_fixed_array():
    ours = encode_args(enc_fixed_array([enc_uint(1), enc_uint(2), enc_uint(3)]))
    ref = eth_abi.encode(["uint256[3]"], [[1, 2, 3]])
    assert ours == ref


# ----- dynamic leaves -----------------------------------------------------------


@pytest.mark.parametrize("data", [b"", b"\x01", b"\x01" * 31, b"\x02" * 32, b"\x03" * 33])
def test_bytes_padding_boundaries(data: bytes):
    ours = encode_args(enc_bytes(data))
    ref = eth_abi.encode(["bytes"], [data])
    assert ours == ref


def test_string():
    ours = encode_args(enc_string("hello venom"))
    ref = eth_abi.encode(["string"], ["hello venom"])
    assert ours == ref


# ----- dynamic arrays -----------------------------------------------------------


def test_uint_dyn_array():
    ours = encode_args(enc_dyn_array([enc_uint(x) for x in (10, 20, 30)]))
    ref = eth_abi.encode(["uint256[]"], [[10, 20, 30]])
    assert ours == ref


def test_empty_dyn_array():
    ours = encode_args(enc_dyn_array([]))
    ref = eth_abi.encode(["uint256[]"], [[]])
    assert ours == ref


def test_batch_transfer_style():
    """(address[], uint256[]) — the batch-transfer calldata shape."""
    tos = [A, B]
    vals = [3, 4]
    ours = encode_args(
        enc_dyn_array([enc_address(a) for a in tos]),
        enc_dyn_array([enc_uint(v) for v in vals]),
    )
    ref = eth_abi.encode(["address[]", "uint256[]"], [[_addr_str(a) for a in tos], vals])
    assert ours == ref


def test_bytes_dyn_array():
    """bytes[] — dynamic elements inside a dynamic array (two-level offsets)."""
    items = [b"\x01\x02", b"", b"\x03" * 40]
    ours = encode_args(enc_dyn_array([enc_bytes(b) for b in items]))
    ref = eth_abi.encode(["bytes[]"], [items])
    assert ours == ref


# ----- mixed static/dynamic + nested tuples --------------------------------------


def test_mixed_static_dynamic_args():
    """(address, uint256, bytes) — static head slots around a dynamic tail."""
    ours = encode_args(enc_address(A), enc_uint(9), enc_bytes(b"note"))
    ref = eth_abi.encode(["address", "uint256", "bytes"], [_addr_str(A), 9, b"note"])
    assert ours == ref


def test_dynamic_tuple():
    """(address, uint256, bytes) as ONE struct argument — dynamic tuple frame."""
    ours = encode_args(enc_tuple(enc_address(A), enc_uint(9), enc_bytes(b"note")))
    ref = eth_abi.encode(["(address,uint256,bytes)"], [(_addr_str(A), 9, b"note")])
    assert ours == ref


def test_array_of_dynamic_structs():
    """(uint256, bytes)[] — nested frames: array offsets over tuple offsets."""
    items = [(1, b"\xaa"), (2, b"\xbb" * 33)]
    ours = encode_args(enc_dyn_array([enc_tuple(enc_uint(u), enc_bytes(b)) for u, b in items]))
    ref = eth_abi.encode(["(uint256,bytes)[]"], [items])
    assert ours == ref


def test_struct_of_struct():
    """((address,uint256),bytes) — struct nested in a struct."""
    ours = encode_args(enc_tuple(enc_tuple(enc_address(A), enc_uint(3)), enc_bytes(b"xy")))
    ref = eth_abi.encode(["((address,uint256),bytes)"], [((_addr_str(A), 3), b"xy")])
    assert ours == ref


# ----- calldata assembly ----------------------------------------------------------


def test_encode_call_prepends_selector():
    sel = erc20_abi.selector("transfer(address,uint256)")
    assert sel == erc20_abi.TRANSFER
    data = encode_call(sel, enc_address(B), enc_uint(1))
    assert data[:4] == sel
    assert data[4:] == eth_abi.encode(["address", "uint256"], [_addr_str(B), 1])
