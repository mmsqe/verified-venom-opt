"""Unit tests for the ``venom-opt`` command line.

The CLI was the one module with no coverage at all, despite being the tool's
user-facing entry point. It is unit-testable without subprocesses: ``main`` takes
an explicit ``argv`` and every ``cmd_*`` returns an exit code rather than calling
``sys.exit``.

These pin the parts a user can actually break: how the optimized map's SLOT is
resolved (``--slot`` > ``--map`` > storageLayout ``balanceOf`` > the hardcoded
default), that ``--slot`` accepts hex, that each subcommand dispatches to its own
handler, and that failures exit non-zero rather than raising.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from venom_opt import balance_patch as bp
from venom_opt import cli

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
ERC20 = ARTIFACTS / "erc20.json"
TWOMAPS = ARTIFACTS / "twomaps.json"


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def test_every_subcommand_is_reachable_and_bound_to_a_handler() -> None:
    """Each advertised subcommand parses and binds a distinct handler."""
    ap = cli.build_parser()
    expected = {
        "compile": cli.cmd_compile,
        "sites": cli.cmd_sites,
        "patch": cli.cmd_patch,
        "demo": cli.cmd_demo,
        "irpatch": cli.cmd_irpatch,
        "verify": cli.cmd_verify,
    }
    for name, fn in expected.items():
        argv = [name, "x.vy"] if name in ("compile", "irpatch") else [name]
        assert ap.parse_args(argv).func is fn, name


def test_missing_subcommand_exits_rather_than_defaulting() -> None:
    """`required=True` on the subparser: no command must be an error, not a
    silent default (which would let `venom-opt` mutate an artifact by accident)."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


@pytest.mark.parametrize(
    ("text", "want"),
    [("5", 5), ("0x5", 5), ("0x0", 0), ("0xdeadbeef", 0xDEADBEEF), ("0o17", 0o17)],
)
def test_slot_accepts_hex_and_decimal(text: str, want: int) -> None:
    """`--slot` is parsed with `int(s, 0)`, so storage slots may be given in the
    hex form users read out of a storageLayout."""
    assert cli.build_parser().parse_args(["sites", "--slot", text]).slot == want


def test_slot_rejects_non_numeric() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["sites", "--slot", "balanceOf"])


def test_artifact_defaults_and_is_overridable() -> None:
    ap = cli.build_parser()
    assert ap.parse_args(["sites"]).artifact == "artifacts/erc20.json"
    assert ap.parse_args(["sites", "other.json"]).artifact == "other.json"


def test_irpatch_map_flags_accumulate() -> None:
    """`--map`/`--mwmap` are `append`: several maps in one run (the multi-map
    case) must collect, not overwrite."""
    args = cli.build_parser().parse_args(["irpatch", "c.vy", "--map", "a=1", "--map", "b=2", "--mwmap", "s"])
    assert args.map == ["a=1", "b=2"]
    assert args.mwmap == ["s"]


# --------------------------------------------------------------------------- #
# _load: slot resolution precedence
# --------------------------------------------------------------------------- #


def _ns(**kw) -> argparse.Namespace:
    kw.setdefault("artifact", str(ERC20))
    kw.setdefault("slot", None)
    kw.setdefault("map", None)
    return argparse.Namespace(**kw)


def test_load_returns_the_artifact_runtime() -> None:
    rt, _ = cli._load(_ns())
    assert rt == bp.runtime_from_artifact(ERC20)
    assert len(rt) > 0


def test_explicit_slot_wins_over_everything() -> None:
    _, slot = cli._load(_ns(slot=0x1234, map="balanceOf"))
    assert slot == 0x1234


def test_map_name_resolves_via_storage_layout() -> None:
    _, slot = cli._load(_ns(map="balanceOf"))
    assert slot == bp.map_slot_from_artifact(ERC20, "balanceOf")


def test_defaults_to_balance_slot_from_the_layout() -> None:
    """With neither --slot nor --map, the balanceOf slot comes from the artifact."""
    _, slot = cli._load(_ns())
    assert slot == bp.balance_slot_from_artifact(ERC20)


def test_falls_back_to_the_hardcoded_slot_when_layout_lacks_balanceof(
    tmp_path: Path,
) -> None:
    """A contract with no balanceOf must not crash the CLI: `_load` swallows
    KeyError/FileNotFoundError and falls back to BALANCE_SLOT."""
    art = json.loads(ERC20.read_text())
    layout = art.get("layout") or art.get("storageLayout") or {}
    if isinstance(layout, dict):
        for key in ("storage_layout", "storageLayout"):
            if isinstance(layout.get(key), dict):
                layout[key].pop("balanceOf", None)
        layout.pop("balanceOf", None)
    stripped = tmp_path / "nobalance.json"
    stripped.write_text(json.dumps(art))
    _, slot = cli._load(_ns(artifact=str(stripped)))
    assert slot == bp.BALANCE_SLOT


# --------------------------------------------------------------------------- #
# dispatch / exit codes
# --------------------------------------------------------------------------- #


def test_sites_reports_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["sites", str(ERC20)]) == 0
    assert capsys.readouterr().out.strip() != ""


def test_demo_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["demo", str(ERC20)]) == 0
    assert capsys.readouterr().out.strip() != ""


def test_patch_writes_runtime_hex(tmp_path: Path) -> None:
    out = tmp_path / "patched.hex"
    assert cli.main(["patch", str(ERC20), "-o", str(out)]) == 0
    text = out.read_text().strip()
    assert text
    bytes.fromhex(text.removeprefix("0x"))  # must be valid hex


def test_patch_is_a_real_rewrite_not_a_copy(tmp_path: Path) -> None:
    """The whole point of the pass: the patched runtime must DIFFER from the
    input. A no-op that still exits 0 would otherwise look like success."""
    out = tmp_path / "patched.hex"
    cli.main(["patch", str(ERC20), "-o", str(out)])
    patched = bytes.fromhex(out.read_text().strip().removeprefix("0x"))
    assert patched != bp.runtime_from_artifact(ERC20)
    assert len(patched) == len(bp.runtime_from_artifact(ERC20))


def test_main_returns_the_handler_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` must return the handler's code verbatim — the shell contract. Patch
    the handler that `build_parser` binds and check the code travels back out."""
    called: dict[str, object] = {}

    def fake(args: argparse.Namespace) -> int:
        called["slot"] = args.slot
        return 7

    monkeypatch.setattr(cli, "cmd_sites", fake)
    assert cli.main(["sites", "--slot", "0x9"]) == 7
    assert called["slot"] == 9


@pytest.mark.parametrize(("checks", "code"), [(True, 0), (False, 1)])
def test_verify_maps_the_proof_result_to_an_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    checks: bool,
    code: int,
) -> None:
    """`cmd_verify` turns the Lean check into a shell exit code, and names the
    theorems it checked. Patched at `verified.verify` — the real seam, which
    shells out to `lake build` — so this exercises cmd_verify's own logic rather
    than a stub of itself."""
    import venom_opt.verified as verified

    monkeypatch.setattr(verified, "verify", lambda d=None: checks)
    assert cli.main(["verify"]) == code
    out = capsys.readouterr().out
    assert ("OK" if checks else "FAILED") in out
    for thm in verified.THEOREMS:
        assert thm in out


def test_verify_reports_every_theorem_it_claims_to_check(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A silently shrinking THEOREMS tuple would weaken the claim without
    failing anything; pin that all five soundness theorems are named."""
    import venom_opt.verified as verified

    monkeypatch.setattr(verified, "verify", lambda d=None: True)
    cli.main(["verify"])
    out = capsys.readouterr().out
    assert len(verified.THEOREMS) == 5
    assert "optSlot_injective" in out and "read_own_write" in out


def test_verify_raises_when_there_is_no_lean_project(tmp_path: Path) -> None:
    """`--dir` pointing somewhere without a lakefile is a user error and must
    say so, rather than silently reporting a passing proof."""
    from venom_opt.verified import verify

    with pytest.raises(FileNotFoundError):
        verify(tmp_path)


# --------------------------------------------------------------------------- #
# irpatch guards — these are SOUNDNESS guards, not cosmetics: a wrong packing id
# or a non-word value type would make the rewrite unsound, so each must refuse
# (exit 1) rather than proceed. Patched at the vyper/IR seams so the guard logic
# is exercised without a compiler in the loop.
# --------------------------------------------------------------------------- #


def _fake_layout(monkeypatch: pytest.MonkeyPatch, layout: dict) -> None:
    import subprocess

    from venom_opt import ir_pass

    monkeypatch.setattr(ir_pass, "runtime_ir_from_contract", lambda c: "IR", raising=False)

    class _R:
        stdout = json.dumps({"storage_layout": layout})

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())


def test_irpatch_refuses_a_non_word_valued_map(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--map` on a struct/String-valued map must be refused: packSlot's scheme
    assumes a single-word value (multi-word maps go through --mwmap)."""
    _fake_layout(monkeypatch, {"m": {"slot": 3, "type": "HashMap[address, String[32]]"}})
    assert cli.main(["irpatch", "c.vy", "--map", "m"]) == 1
    assert "not a single word" in capsys.readouterr().err


def test_irpatch_refuses_when_nothing_was_requested(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No --map and no --mwmap is a no-op; exit 1 rather than silently emit an
    unoptimized runtime that looks optimized."""
    _fake_layout(monkeypatch, {})
    assert cli.main(["irpatch", "c.vy"]) == 1
    assert "nothing to do" in capsys.readouterr().err


def test_irpatch_refuses_multiple_maps_sharing_id_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE soundness guard: id 0 means '~key aliases', so two maps both at id 0
    would collide in the same optimized slots. Must refuse."""
    _fake_layout(
        monkeypatch,
        {
            "a": {"slot": 1, "type": "HashMap[address, uint256]"},
            "b": {"slot": 2, "type": "HashMap[address, uint256]"},
        },
    )
    assert cli.main(["irpatch", "c.vy", "--map", "a", "--map", "b"]) == 1
    err = capsys.readouterr().err
    assert "DISTINCT nonzero ids" in err


def test_irpatch_refuses_when_only_one_of_several_maps_has_id_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mixed ids where ANY is 0 is equally unsound — the guard is `any(v == 0)`,
    not `all`."""
    _fake_layout(
        monkeypatch,
        {
            "a": {"slot": 1, "type": "HashMap[address, uint256]"},
            "b": {"slot": 2, "type": "HashMap[address, uint256]"},
        },
    )
    assert cli.main(["irpatch", "c.vy", "--map", "a=1", "--map", "b"]) == 1
    assert "DISTINCT nonzero ids" in capsys.readouterr().err


def test_irpatch_accepts_distinct_nonzero_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive case must NOT be refused — otherwise the guards above would
    pass vacuously (a CLI that always exits 1 satisfies every refusal test)."""
    from venom_opt import ir_pass

    _fake_layout(
        monkeypatch,
        {
            "a": {"slot": 1, "type": "HashMap[address, uint256]"},
            "b": {"slot": 2, "type": "HashMap[address, uint256]"},
        },
    )
    seen: dict[str, object] = {}

    def fake_opt(src: str, slot_to_id: dict[int, int]) -> tuple[str, int]:
        seen["slot_to_id"] = dict(slot_to_id)
        return src, 2

    monkeypatch.setattr(ir_pass, "optimize_ir", fake_opt, raising=False)
    monkeypatch.setattr(ir_pass, "compile_ir", lambda ir: b"\x60\x00", raising=False)
    assert cli.main(["irpatch", "c.vy", "--map", "a=1", "--map", "b=2"]) == 0
    assert seen["slot_to_id"] == {1: 1, 2: 2}
