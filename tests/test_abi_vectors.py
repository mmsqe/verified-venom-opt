"""The Python encoder reproduces evm-abi-lean's verified calldata, byte for byte.

``tests/vectors/abi_lean_vectors.json`` is generated out-of-process by
``scripts/gen_abi_vectors.py`` from the *verified* encoder in evm-abi-lean
(``functionSelector`` + ``encodeArgs``, roundtrip-proved by
``roundtrip_args_wff``). These tests pin :mod:`venom_opt.abi` (and the
selector helper) to those bytes — so the calldata the differential tests
drive is not merely eth_abi-shaped, it is the verified encoder's output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from venom_opt.abi import (
    Enc,
    enc_address,
    enc_bytes,
    enc_dyn_array,
    enc_tuple,
    enc_uint,
    encode_call,
)
from venom_opt.erc20_abi import selector

VECTORS_FILE = Path(__file__).resolve().parent / "vectors" / "abi_lean_vectors.json"
VECTORS = json.loads(VECTORS_FILE.read_text())["vectors"]


def load_vector_calldata() -> dict[str, bytes]:
    """name -> verified calldata bytes (shared with the differential tests)."""
    return {v["name"]: bytes.fromhex(v["calldata"][2:]) for v in VECTORS}


def _enc(node: dict) -> Enc:
    """Rebuild one typed args node from the vectors file with venom_opt.abi."""
    t, v = node["type"], node["value"]
    if t == "address":
        return enc_address(bytes.fromhex(v[2:]))
    if t == "uint256":
        return enc_uint(int(v))
    if t == "bytes":
        return enc_bytes(bytes.fromhex(v[2:]))
    if t == "address[]":
        return enc_dyn_array([enc_address(bytes.fromhex(x[2:])) for x in v])
    if t == "uint256[]":
        return enc_dyn_array([enc_uint(int(x)) for x in v])
    if t == "tuple":
        return enc_tuple(*[_enc(n) for n in v])
    raise ValueError(f"unknown vector arg type {t}")


@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_python_encoder_matches_abi_lean(vec: dict):
    ours = encode_call(selector(vec["signature"]), *[_enc(n) for n in vec["args"]])
    assert ours == bytes.fromhex(vec["calldata"][2:]), (
        f"venom_opt.abi drifted from evm-abi-lean's verified encoding for {vec['name']}"
    )


def test_vectors_cover_every_dynamic_signature():
    """The vector set spans all three dynamic-argument entry points of ERC20Dyn."""
    sigs = {v["signature"] for v in VECTORS}
    assert "batch_transfer(address[],uint256[])" in sigs
    assert "transfer_with_note(address,uint256,bytes)" in sigs
    assert "pay((address,uint256))" in sigs
