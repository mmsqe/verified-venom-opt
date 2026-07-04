"""The value-type guard: only single-word-value maps are patchable.

A multi-word map value (struct, ``String[..]``/``Bytes[..]``) lives at
keccak-base+i; the length-preserving ``~key`` rewrite packs bases densely
(``~(k+1) = ~k - 1``), so adjacent keys' value windows would interleave —
deterministic corruption (empirically: a patched
``HashMap[address, String[64]]`` map explodes with out-of-gas on its first
write). ``map_slot_from_artifact`` refuses such maps; the sound multi-word
layout (``strideSlot``) is proved in EVMYulLean's SlotPacking.lean and awaits
the IR-level pass.

The differential half checks coexistence: patching balanceOf leaves the
dynamic-VALUE map byte-identical and behaviourally equal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from venom_opt import balance_patch as bp

DYNVALUE = Path(__file__).resolve().parent.parent / "artifacts" / "dynvalue.json"
MIXEDKEYS = Path(__file__).resolve().parent.parent / "artifacts" / "mixedkeys.json"


# ----- the guard ---------------------------------------------------------------

def test_refuses_dynamic_value_map():
    with pytest.raises(ValueError, match="spans\nmultiple storage words|spans"):
        bp.map_slot_from_artifact(DYNVALUE, "notes")


def test_allows_word_value_maps():
    assert bp.map_slot_from_artifact(DYNVALUE, "balanceOf") == 0
    # dynamic KEY with a word value is fine (Tier B) — key vs value asymmetry
    assert bp.map_slot_from_artifact(MIXEDKEYS, "names") == 1
    assert bp.map_slot_from_artifact(MIXEDKEYS, "tags") == 2


def test_refuses_non_hashmap(tmp_path):
    import json

    art = tmp_path / "art.json"
    art.write_text(json.dumps({"storageLayout": {"totalSupply": {"type": "uint256", "slot": 3}}}))
    with pytest.raises(ValueError, match="not a HashMap"):
        bp.map_slot_from_artifact(art, "totalSupply")


def test_value_type_parser():
    f = bp._hashmap_value_type
    assert f("HashMap[address, uint256]") == "uint256"
    assert f("HashMap[String[64], uint256]") == "uint256"
    assert f("HashMap[address, String[64]]") == "String[64]"
    # nested map values parse to the inner HashMap (and are refused as non-word)
    assert f("HashMap[address, HashMap[address, uint256]]") == "HashMap[address, uint256]"
    assert f("uint256") is None


# ----- coexistence: balanceOf patched, dynamic-VALUE map untouched --------------

boa = pytest.importorskip("boa")

from venom_opt.abi import enc_address, enc_string, encode_call  # noqa: E402
from venom_opt.erc20_abi import arg_addr, selector, word  # noqa: E402

SET_NOTE = selector("set_note(address,string)")
NOTES = selector("notes(address)")
MINT = selector("mint(address,uint256)")
BALANCE_OF = selector("balanceOf(address)")

# adjacent keys — exactly the pair the ~key scheme would corrupt
A = (0x1000).to_bytes(20, "big")
A1 = (0x1001).to_bytes(20, "big")


def test_balance_patch_coexists_with_dynamic_value_map():
    creation = bp.creation_from_artifact(DYNVALUE)
    runtime = bp.runtime_from_artifact(DYNVALUE)
    patched = bp.patch(runtime, bp.map_slot_from_artifact(DYNVALUE, "balanceOf"))
    # the notes map's derivations are untouched
    assert bp.count_sites(patched, 1) == bp.count_sites(runtime, 1)

    orig, _ = boa.env.deploy_code(bytecode=creation)
    opt, _ = boa.env.deploy_code(bytecode=creation)
    boa.env.set_code(opt, patched)
    note = "x" * 60  # spans length word + 2 data words
    for c in (orig, opt):
        boa.env.raw_call(c, data=MINT + arg_addr(A) + word(7))
        boa.env.raw_call(c, data=encode_call(SET_NOTE, enc_address(A), enc_string(note)))
        boa.env.raw_call(c, data=encode_call(SET_NOTE, enc_address(A1), enc_string(note)))
    for probe in (NOTES + arg_addr(A), NOTES + arg_addr(A1), BALANCE_OF + arg_addr(A)):
        assert bytes(boa.env.raw_call(orig, data=probe).output) == bytes(
            boa.env.raw_call(opt, data=probe).output
        )
    assert note.encode() in bytes(boa.env.raw_call(opt, data=NOTES + arg_addr(A)).output)
