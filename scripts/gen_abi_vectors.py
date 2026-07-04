#!/usr/bin/env python3
"""Regenerate tests/vectors/abi_lean_vectors.json from evm-abi-lean.

Out-of-process vector generation: every dynamic-calldata byte string the
differential tests drive (DynArray batch transfers, Bytes notes, a struct
argument) is produced by the *verified* encoder in the evm-abi-lean
development (``functionSelector`` + ``encodeArgs``, the encoder whose
encode→decode roundtrip is proved by ``roundtrip_args_wff``), not by the
in-repo Python encoder. The Python encoder (:mod:`venom_opt.abi`) is then
*checked against* these vectors byte-for-byte in ``tests/test_abi_vectors.py``.

The default source is a **pinned git commit** (``ABI_LEAN_REV`` below), cloned
into a cache on first use — reproducible on any machine, no local checkout
required. The two repos pin different Lean toolchains, so — same pattern as
EVMYulLean's ``scripts/abi_crossval.sh`` — this shells out to ``lake env lean``
inside that checkout instead of taking a lake dependency. (First git run also
materializes abi-lean's lake deps into the cache; subsequent runs are cheap.)

Usage:
    python3 scripts/gen_abi_vectors.py            # the pinned commit (cached clone)
    ABI_LEAN=/path/to/evm-abi-lean ...            # a local checkout (e.g. unpushed work)
    ABI_LEAN_REV=<sha> / ABI_LEAN_GIT=<url> ...   # a different pin / fork
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "tests" / "vectors" / "abi_lean_vectors.json"

#: The verified encoder's source: a pinned evm-abi-lean commit. Bump the pin
#: (and regenerate) deliberately — the JSON records it as provenance.
ABI_LEAN_GIT = "https://github.com/yihuang/evm-abi-lean.git"
ABI_LEAN_REV = "0bf9104ad632c639ab6109baecbbb2de47ddbe64"

ONE = 10**18
A, B, C, D = ("0x" + x.to_bytes(20, "big").hex() for x in (0xAA, 0xBB, 0xCC, 0xDD))


#: Typed-node builders for the vector spec. The same nodes are written into
#: the vectors file, so the cross-check test can rebuild each encoding with
#: venom_opt.abi without duplicating the spec.
def _addr(v: str) -> dict:
    return {"type": "address", "value": v}


def _u(x: int) -> dict:
    return {"type": "uint256", "value": str(x)}


def _addrs(*vs: str) -> dict:
    return {"type": "address[]", "value": list(vs)}


def _us(*xs: int) -> dict:
    return {"type": "uint256[]", "value": [str(x) for x in xs]}


def _data(b: bytes) -> dict:
    return {"type": "bytes", "value": "0x" + b.hex()}


def _str(v: str) -> dict:
    return {"type": "string", "value": v}


def _tup(*nodes: dict) -> dict:
    return {"type": "tuple", "value": list(nodes)}


def _batch(name: str, tos: list[str], vals: list[int]) -> tuple:
    return (name, "batch_transfer(address[],uint256[])", [_addrs(*tos), _us(*vals)])


def _note(name: str, note: bytes) -> tuple:
    return (
        name,
        "transfer_with_note(address,uint256,bytes)",
        [_addr(B), _u(ONE), _data(note)],
    )


#: (name, signature, args) — every dynamic shape the differential tests drive,
#: plus one static reference vector (the primitive-word path).
VECTORS = [
    ("transfer_b_1", "transfer(address,uint256)", [_addr(B), _u(ONE)]),
    _batch("batch_bcd_321", [B, C, D], [3 * ONE, 2 * ONE, ONE]),
    _batch("batch_empty", [], []),
    _batch("batch_bc_1", [B, C], [ONE]),
    _batch("batch_bc_11", [B, C], [ONE, ONE]),
    _batch("batch_b_1", [B], [ONE]),
    _batch("batch_bcd_111", [B, C, D], [ONE, ONE, ONE]),
    _note("note_empty", b""),
    _note("note_gm", b"gm"),
    _note("note_full", b"\xab" * 64),
    ("pay_c_2", "pay((address,uint256))", [_tup(_addr(C), _u(2 * ONE))]),
    # String-keyed map calls (MixedKeys.vy): dynamic key, two-stage slot hash
    ("set_name_alice_7", "set_name(string,uint256)", [_str("alice"), _u(7)]),
    ("set_name_max_9", "set_name(string,uint256)", [_str("x" * 64), _u(9)]),
    ("names_alice", "names(string)", [_str("alice")]),
    ("bump_name_alice", "bump_name(string)", [_str("alice")]),
]


def _lean_type(node: dict) -> str:
    t = node["type"]
    if t == "address":
        return ".address"
    if t == "uint256":
        return "u256"
    if t == "bytes":
        return ".bytes"
    if t == "string":
        return ".string"
    if t == "address[]":
        return ".array .address"
    if t == "uint256[]":
        return ".array u256"
    if t == "tuple":
        return ".tuple [" + ", ".join(_lean_type(n) for n in node["value"]) + "]"
    raise ValueError(f"unknown type {t}")


def _lean_value(node: dict) -> str:
    t, v = node["type"], node["value"]
    if t == "address":
        return f".address (addr {int(v, 16)})"
    if t == "uint256":
        return f".uint {int(v)}"
    if t == "bytes":
        data = bytes.fromhex(v[2:])
        if not data:
            return ".bytes ByteArray.empty"
        lit = ", ".join(f"0x{b:02x}" for b in data)
        return f".bytes (ByteArray.mk #[{lit}])"
    if t == "string":
        return f'.string "{v}"'  # ASCII vector keys only, no escaping needed
    if t in ("address[]", "uint256[]"):
        elem = "address" if t == "address[]" else "uint256"
        inner = ", ".join(_lean_value({"type": elem, "value": x}) for x in v)
        return f".array [{inner}]"
    if t == "tuple":
        return ".tuple [" + ", ".join(_lean_value(n) for n in v) + "]"
    raise ValueError(f"unknown type {t}")


def _lean_script() -> str:
    evals = []
    for name, sig, args in VECTORS:
        types = "[" + ", ".join(_lean_type(n) for n in args) + "]"
        values = "[" + ", ".join(_lean_value(n) for n in args) + "]"
        evals.append(f"""#eval do
  match encodeArgs {types} {values} with
  | .ok args => IO.println s!"{name} {{hexBytes (functionSelector "{sig}" ++ args)}}"
  | .error e => IO.eprintln s!"{name} ENCODE FAILED: {{repr e}}\"""")
    body = "\n".join(evals)
    return f"""import EvmAbi.Hash
import EvmAbi.ABI
import EvmAbi.Encode
open EvmAbi.Hash EvmAbi.ABI EvmAbi.ABI.Encode

def u256 : ABIType := .uint (ByteSize.ofLen 32 (by omega))
def addr (n : Nat) : ByteArray := (uint256ToBytes n).extract 12 32

{body}
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _resolve_checkout() -> Path:
    """An explicit local checkout (ABI_LEAN) wins; otherwise clone/fetch the
    pinned commit into a persistent cache and detach onto it."""
    if env := os.environ.get("ABI_LEAN"):
        p = Path(env)
        if not (p / "EvmAbi").is_dir():
            sys.exit(f"FATAL: no evm-abi-lean checkout at {p}")
        return p
    url = os.environ.get("ABI_LEAN_GIT", ABI_LEAN_GIT)
    rev = os.environ.get("ABI_LEAN_REV", ABI_LEAN_REV)
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    root = cache / "verified-venom-opt" / "abi-lean"
    if not (root / ".git").is_dir():
        root.mkdir(parents=True, exist_ok=True)
        _git(root, "init", "-q")
        _git(root, "remote", "add", "origin", url)
    if (
        subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{rev}^{{commit}}"],
            capture_output=True,
        ).returncode
        != 0
    ):
        _git(root, "fetch", "--depth", "1", "origin", rev)
    _git(root, "checkout", "-q", "--detach", rev)
    return root


def main() -> int:
    abi_lean = _resolve_checkout()
    subprocess.run(
        ["lake", "build", "EvmAbi.Hash", "EvmAbi.ABI", "EvmAbi.Encode"],
        cwd=abi_lean,
        check=True,
        capture_output=True,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False) as f:
        f.write(_lean_script())
        gen = f.name
    try:
        out = subprocess.run(
            ["lake", "env", "lean", gen],
            cwd=abi_lean,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    finally:
        os.unlink(gen)

    produced = dict(
        line.split(" ", 1)
        for line in out.splitlines()
        if " 0x" in line and line.split(" ", 1)[0] in {v[0] for v in VECTORS}
    )
    missing = [name for name, _, _ in VECTORS if name not in produced]
    if missing:
        print(
            f"FATAL: abi-lean produced no calldata for {missing}\n{out}",
            file=sys.stderr,
        )
        return 1

    sha = subprocess.run(
        ["git", "-C", str(abi_lean), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generator": "scripts/gen_abi_vectors.py (evm-abi-lean functionSelector + encodeArgs)",
                "abi_lean_commit": sha or None,
                "vectors": [
                    {
                        "name": name,
                        "signature": sig,
                        "args": args,
                        "calldata": produced[name],
                    }
                    for name, sig, args in VECTORS
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {OUT.relative_to(HERE)} ({len(VECTORS)} vectors, abi-lean @ {sha})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
