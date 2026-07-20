#!/usr/bin/env python3
"""Regenerate tests/vectors/abi_lean_vectors.json from evm-abi-lean.

Out-of-process vector generation: every dynamic-calldata byte string the
differential tests drive (DynArray batch transfers, Bytes notes, a struct
argument) has its **argument region** produced by the *verified* encoder in
evm-abi-lean (``encode`` at ``.tuple``, whose encode→decode roundtrip is proved
by ``roundtrip``), not by the in-repo Python encoder. The 4-byte selector is
prepended from the pinned ``SELECTORS`` table — keccak lives outside that
library, whose scope is the codec roundtrip. The Python encoder (:mod:`venom_opt.abi`) is then
*checked against* these vectors byte-for-byte in ``tests/test_abi_vectors.py``.

The emitted Lean targets abi-lean's **Ty-indexed** codec (``EvmAbi.Ty`` /
``EvmAbi.Codec``), which replaced the earlier ``ABIType``/``ABIValue`` pair. Two
consequences for this script: encoding is now total (a ``List UInt8``, not an
``Except``), because a value's type index already rules out ill-typed inputs; and
an argument list *is* the tuple of its types — right-nested ``TupleVal`` — so it
needs no argument-level wrapper from the library. The vectors themselves are unchanged — the rewrite reproduces all 15
byte-for-byte.

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
#:
#: The pin is a *published* upstream commit. Only the **argument region** is
#: re-derived from it: an argument list is definitionally the tuple of its types,
#: so `encode (.tuple ts) vs` is all the library needs to provide. The 4-byte
#: selector is NOT taken from abi-lean -- keccak moved out of that library (its
#: scope is the codec roundtrip), so the selectors are pinned below instead.
ABI_LEAN_GIT = "https://github.com/yihuang/evm-abi-lean.git"
ABI_LEAN_REV = "da43ad6ed2c037b593548e65721c674a60fb488e"

#: signature -> 4-byte selector. Pinned rather than computed: ``tests/
#: test_abi_vectors.py`` rebuilds calldata with ``venom_opt.erc20_abi.selector``
#: (vyper's ``method_id``) and compares against these vectors, so deriving them
#: from that same helper here would make the selector half of that assertion
#: circular. These values are independently proved to equal ``keccak256(sig)[:4]``
#: by EVMYulLean's ``AbiCrossval.erc20_selectors_match_keccak`` (``decide +kernel``).
SELECTORS = {
    "batch_transfer(address[],uint256[])": "833eccc5",
    "bump_name(string)": "00d87f62",
    "names(string)": "822fbf58",
    "pay((address,uint256))": "0c518a32",
    "set_name(string,uint256)": "1d3e3cde",
    "transfer(address,uint256)": "a9059cbb",
    "transfer_with_note(address,uint256,bytes)": "2338ddc9",
}

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
    """A vector node's ABI type, as an ``EvmAbi.Ty`` term."""
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
    """A vector node's value, as an inhabitant of ``(_lean_type node).Val``.

    The Ty-indexed value family is refined per type, so there are no value
    constructors to name: an ``address`` is a bounded ``Nat`` subtype, ``bytes``
    is a plain ``List UInt8``, an array is a ``List`` of element values, and a
    tuple is the right-nested product ``TupleVal`` (hence the trailing ``⟨⟩``).
    """
    t, v = node["type"], node["value"]
    if t == "address":
        return f"(addr {int(v, 16)})"
    if t == "uint256":
        return f"(u {int(v)})"
    if t == "bytes":
        data = bytes.fromhex(v[2:])
        if not data:
            return "([] : List UInt8)"
        lit = ", ".join(f"0x{b:02x}" for b in data)
        return f"([{lit}] : List UInt8)"
    if t == "string":
        return f'("{v}" : String)'  # ASCII vector keys only, no escaping needed
    if t in ("address[]", "uint256[]"):
        elem = "address" if t == "address[]" else "uint256"
        return "[" + ", ".join(_lean_value({"type": elem, "value": x}) for x in v) + "]"
    if t == "tuple":
        return "(" + "".join(_lean_value(n) + ", " for n in v) + "⟨⟩)"
    raise ValueError(f"unknown type {t}")


def _lean_script() -> str:
    evals = []
    for name, _sig, args in VECTORS:
        types = "[" + ", ".join(_lean_type(n) for n in args) + "]"
        # the argument tuple is itself a TupleVal, so it too ends in ⟨⟩
        values = "(" + "".join(_lean_value(n) + ", " for n in args) + "⟨⟩)"
        # Plain ``++`` rather than s!-interpolation: an interpolation hole cannot
        # contain a nested string literal, and the signature is one.
        evals.append(f'#eval IO.println ("{name} 0x" ++ hexBytes (EvmAbi.encode (.tuple {types}) {values}))')
    body = "\n".join(evals)
    return f"""import EvmAbi.Codec
open EvmAbi

def u256 : Ty := .uint 256

/-- Values are refined subtypes in the Ty-indexed family; the vector spec only
carries in-range numbers, so the bound is discharged by construction. -/
def addr (n : Nat) : Ty.Val .address := ⟨n % 2 ^ 160, Nat.mod_lt _ (by decide)⟩
def u (n : Nat) : Ty.Val u256 := ⟨n % 2 ^ 256, Nat.mod_lt _ (by decide)⟩

def hexBytes (bs : List UInt8) : String :=
  String.join (bs.map fun b =>
    let s := Nat.toDigits 16 b.toNat
    (if s.length == 1 then "0" else "") ++ String.ofList s)

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
        try:
            _git(root, "fetch", "--depth", "1", "origin", rev)
        except subprocess.CalledProcessError as exc:
            # Much the likeliest cause is a pin that was never pushed (see the
            # ABI_LEAN_REV note above); a raw traceback buries that.
            sys.exit(
                f"FATAL: cannot fetch evm-abi-lean {rev[:7]} from {url}\n"
                f"  git said: {exc.stderr.strip() or exc}\n"
                "  If that rev is unpushed, regenerate from a local checkout instead:\n"
                "      ABI_LEAN=/path/to/evm-abi-lean make vectors"
            )
    _git(root, "checkout", "-q", "--detach", rev)
    return root


def main() -> int:
    abi_lean = _resolve_checkout()
    subprocess.run(
        ["lake", "build", "EvmAbi.Codec"],
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
                "generator": "scripts/gen_abi_vectors.py (evm-abi-lean encode at .tuple; selectors pinned)",
                "abi_lean_commit": sha or None,
                "vectors": [
                    {
                        "name": name,
                        "signature": sig,
                        "args": args,
                        "calldata": "0x" + SELECTORS[sig] + produced[name][2:],
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
