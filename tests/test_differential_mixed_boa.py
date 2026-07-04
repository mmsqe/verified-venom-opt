"""Coexistence differential tests (MixedKeys.vy): patch ONE map, touch nothing else.

MixedKeys.vy holds three maps: address-keyed ``balanceOf`` (slot 0),
String-keyed ``names`` (slot 1 — Venom derives its slot in two stages: an
inner variable-size keccak of the key bytes, then the standard 64-byte outer
keccak), and bytes32-keyed ``tags`` (slot 2 — single-word key, the exact
balanceOf shape).

Tier A (this module's ``instances`` fixture): patch **balanceOf only** and
assert full behavioural parity on all three maps — the peephole coexists with
dynamic-keyed maps. The String-map calldata comes from the verified
evm-abi-lean vectors.

Skipped automatically if titanoboa is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

boa = pytest.importorskip("boa")  # skip the whole module if titanoboa is absent

from venom_opt import balance_patch as bp  # noqa: E402
from venom_opt.erc20_abi import arg_addr, selector, word  # noqa: E402

from test_abi_vectors import load_vector_calldata  # noqa: E402

ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "mixedkeys.json"
ONE = 10**18

VEC = load_vector_calldata()

MINT = selector("mint(address,uint256)")
TRANSFER = selector("transfer(address,uint256)")
BALANCE_OF = selector("balanceOf(address)")
SET_TAG = selector("set_tag(bytes32,uint256)")
TAGS = selector("tags(bytes32)")

A = (0xAA).to_bytes(20, "big")
B = (0xBB).to_bytes(20, "big")
TAG_KEY = (0xC0FFEE).to_bytes(32, "big")


def _slots() -> dict[str, int]:
    layout = json.loads(ARTIFACT.read_text())["storageLayout"]
    return {k: int(v["slot"]) for k, v in layout.items()}


def _deploy_pair(patch_slot: int):
    """A fresh (original, patched-at-`patch_slot`) pair of MixedKeys runtimes."""
    creation = bp.creation_from_artifact(ARTIFACT)
    runtime = bp.runtime_from_artifact(ARTIFACT)
    patched = bp.patch(runtime, patch_slot)
    orig, _ = boa.env.deploy_code(bytecode=creation)
    opt, _ = boa.env.deploy_code(bytecode=creation)
    boa.env.set_code(opt, patched)
    return orig, opt


@pytest.fixture
def instances():
    return _deploy_pair(_slots()["balanceOf"])


def _call(c, data: bytes, sender: bytes | None = None):
    return boa.env.raw_call(c, sender=sender, data=data)


def _out(c, data: bytes, sender: bytes | None = None) -> bytes:
    return bytes(_call(c, data, sender=sender).output)


# ----- patcher-level: per-map sites, and patching one map spares the others ----

def test_sites_per_map():
    runtime = bp.runtime_from_artifact(ARTIFACT)
    slots = _slots()
    counts = {name: bp.count_sites(runtime, slot) for name, slot in slots.items()}
    # every map's OUTER 64-byte keccak is recognized at its own slot — including
    # the String map's (its inner variable-size keccak is never matched)
    assert all(c > 0 for c in counts.values()), counts
    # patching balanceOf leaves the other maps' derivations byte-identical
    patched = bp.patch(runtime, slots["balanceOf"])
    assert bp.count_sites(patched, slots["balanceOf"]) == 0
    for other in ("names", "tags"):
        assert bp.count_sites(patched, slots[other]) == counts[other]


# ----- Tier A: balanceOf patched; ALL maps behave identically -------------------

def test_balance_parity(instances):
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(5 * ONE))
        _call(c, TRANSFER + arg_addr(B) + word(2 * ONE), sender=A)
    for who in (A, B):
        assert _out(orig, BALANCE_OF + arg_addr(who)) == _out(opt, BALANCE_OF + arg_addr(who))


def test_string_map_parity(instances):
    """The String-keyed map (untouched by the patch) behaves identically —
    driven by verified evm-abi-lean calldata, incl. the 64-char boundary key."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, VEC["set_name_alice_7"])
        _call(c, VEC["set_name_max_9"])
    assert _out(orig, VEC["names_alice"]) == _out(opt, VEC["names_alice"]) == word(7)
    r_orig = _out(orig, VEC["bump_name_alice"])
    r_opt = _out(opt, VEC["bump_name_alice"])
    assert r_orig == r_opt == word(8)


def test_bytes32_map_parity(instances):
    orig, opt = instances
    for c in (orig, opt):
        _call(c, SET_TAG + TAG_KEY + word(42))
    assert _out(orig, TAGS + TAG_KEY) == _out(opt, TAGS + TAG_KEY) == word(42)


def test_maps_do_not_interfere(instances):
    """Writes across all three maps interleaved: full observable parity."""
    orig, opt = instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(ONE))
        _call(c, VEC["set_name_alice_7"])
        _call(c, SET_TAG + TAG_KEY + word(1))
        _call(c, VEC["bump_name_alice"])
        _call(c, TRANSFER + arg_addr(B) + word(ONE), sender=A)
    for probe in (
        BALANCE_OF + arg_addr(A),
        BALANCE_OF + arg_addr(B),
        VEC["names_alice"],
        TAGS + TAG_KEY,
    ):
        assert _out(orig, probe) == _out(opt, probe)


# ----- Tier B: optimize the DYNAMIC-keyed map itself -----------------------------
#
# patch() pointed at the String map's slot rewrites its OUTER 64-byte keccak
# to ~innerHash — dropping one KECCAK256 per access. Inner-hash collisions
# collide in the original derivation too, so no new assumptions. The balance
# and bytes32 maps stay on their keccak slots (one optimized map per contract).

@pytest.fixture
def names_instances():
    return _deploy_pair(_slots()["names"])


def test_string_map_optimized_parity(names_instances):
    orig, opt = names_instances
    for c in (orig, opt):
        _call(c, VEC["set_name_alice_7"])
        _call(c, VEC["set_name_max_9"])
    assert _out(orig, VEC["names_alice"]) == _out(opt, VEC["names_alice"]) == word(7)
    assert _out(orig, VEC["bump_name_alice"]) == _out(opt, VEC["bump_name_alice"]) == word(8)


def test_string_map_optimized_spares_others(names_instances):
    """With `names` optimized, balanceOf and tags still behave identically."""
    orig, opt = names_instances
    for c in (orig, opt):
        _call(c, MINT + arg_addr(A) + word(ONE))
        _call(c, SET_TAG + TAG_KEY + word(3))
        _call(c, TRANSFER + arg_addr(B) + word(ONE), sender=A)
    for probe in (BALANCE_OF + arg_addr(A), BALANCE_OF + arg_addr(B), TAGS + TAG_KEY):
        assert _out(orig, probe) == _out(opt, probe)


def test_string_map_optimized_gas_not_worse(names_instances):
    """bump_name does a load + a store on the String map — two outer keccaks
    dropped per call once warmed."""
    orig, opt = names_instances
    for c in (orig, opt):
        _call(c, VEC["set_name_alice_7"])  # warm the slot
        _call(c, VEC["bump_name_alice"])
    gO = _call(orig, VEC["bump_name_alice"]).get_gas_used()
    gP = _call(opt, VEC["bump_name_alice"]).get_gas_used()
    assert gP <= gO


@pytest.fixture
def tags_instances():
    return _deploy_pair(_slots()["tags"])


def test_bytes32_map_optimized_parity(tags_instances):
    """Single-word bytes32 keys: the exact balanceOf shape, ~key injective
    outright — patchable today with zero changes."""
    orig, opt = tags_instances
    other_key = (0xDEAD).to_bytes(32, "big")
    for c in (orig, opt):
        _call(c, SET_TAG + TAG_KEY + word(42))
        _call(c, SET_TAG + other_key + word(43))
    for key in (TAG_KEY, other_key):
        assert _out(orig, TAGS + key) == _out(opt, TAGS + key)
    assert _out(opt, TAGS + TAG_KEY) == word(42)
