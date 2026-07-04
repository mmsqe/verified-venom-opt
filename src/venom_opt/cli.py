"""Command-line interface: ``venom-opt <compile|sites|patch|demo|verify>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from venom_opt import balance_patch as bp
from venom_opt.compiler import write_artifact


def _load(args: argparse.Namespace) -> tuple[bytes, int]:
    """Runtime bytecode + the slot of the map to optimize: --slot wins, then
    --map <name> (any word-keyed or hashed-key map from storageLayout), else
    balanceOf."""
    rt = bp.runtime_from_artifact(args.artifact)
    slot = args.slot
    if slot is None and getattr(args, "map", None):
        slot = bp.map_slot_from_artifact(args.artifact, args.map)
    if slot is None:
        try:
            slot = bp.balance_slot_from_artifact(args.artifact)
        except (KeyError, FileNotFoundError):
            slot = bp.BALANCE_SLOT
    return rt, slot


def cmd_compile(args: argparse.Namespace) -> int:
    art = write_artifact(args.contract, args.out, venom=not args.no_venom)
    slot = art["storageLayout"].get("balanceOf", {}).get("slot")
    print(f"compiled {args.contract} -> {args.out}  (balanceOf slot: {slot})")
    return 0


def cmd_sites(args: argparse.Namespace) -> int:
    rt, slot = _load(args)
    print(f"balance sites (slot 0x{slot:02x}): {bp.count_sites(rt, slot)}")
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    rt, slot = _load(args)
    patched = bp.patch(rt, slot)
    print(
        f"slot 0x{slot:02x}: {bp.count_sites(rt, slot)} sites -> "
        f"{bp.count_sites(patched, slot)}, "
        f"length {len(rt)} -> {len(patched)} (preserved: {len(rt) == len(patched)})"
    )
    if args.out:
        Path(args.out).write_text("0x" + patched.hex() + "\n")
        print(f"wrote {args.out}")
    return 0


def cmd_irpatch(args: argparse.Namespace) -> int:
    from venom_opt import ir_pass
    import json

    src = ir_pass.runtime_ir_from_contract(args.contract)
    layout = json.loads(
        __import__("subprocess").run(
            ["vyper", "-f", "layout", str(args.contract)],
            check=True, capture_output=True, text=True,
        ).stdout
    )["storage_layout"]
    # --map NAME[=id] ...  (default id 0); each map's value type is guarded
    slot_to_id: dict[int, int] = {}
    for spec in args.map:
        name, _, id_str = spec.partition("=")
        entry = layout[name]
        vtype = bp._hashmap_value_type(entry.get("type", ""))
        if vtype is None or not bp._WORD_VALUE.match(vtype):
            print(f"refusing {name}: value type {vtype!r} is not a single word", file=sys.stderr)
            return 1
        slot_to_id[int(entry["slot"])] = int(id_str) if id_str else 0
    if not slot_to_id and not args.mwmap:
        print("nothing to do: pass --map and/or --mwmap", file=sys.stderr)
        return 1
    if len(slot_to_id) > 1 and any(v == 0 for v in slot_to_id.values()):
        print("multiple maps need DISTINCT nonzero ids (id 0 = ~key aliases); "
              "use --map name=1 --map other=2", file=sys.stderr)
        return 1
    out_ir, n = ir_pass.optimize_ir(src, slot_to_id) if slot_to_id else (src, 0)
    m = 0
    for name in args.mwmap:
        out_ir, m_i = ir_pass.optimize_ir_multiword(out_ir, int(layout[name]["slot"]))
        m += m_i
    bc = ir_pass.compile_ir(out_ir)
    print(f"IR pass: {n} packSlot site(s) over {len(slot_to_id)} map(s) + "
          f"{m} strideSlot base(s) over {len(args.mwmap)} multi-word map(s); "
          f"runtime {len(bc)} bytes")
    if args.out:
        Path(args.out).write_text("0x" + bc.hex() + "\n")
        print(f"wrote {args.out}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from venom_opt.verified import THEOREMS, verify

    ok = verify(args.dir)
    print(f"soundness proof ({', '.join(THEOREMS)}): {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def cmd_demo(args: argparse.Namespace) -> int:
    rt, slot = _load(args)
    patched = bp.patch(rt, slot)  # raises unless ≥1 site, so sites > 0 below
    sites = bp.count_sites(rt, slot)
    changed = sum(a != b for a, b in zip(rt, patched))
    print(f"Balance slot  : {slot}")
    print(f"Original size : {len(rt)}")
    print(f"Patched size  : {len(patched)}  (length-preserving)")
    print(f"Balance sites : {sites} keccak slot derivations -> ~addr")
    print(f"Changed bytes : {changed}  ({changed // sites} per site)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="venom-opt", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compile", help="Vyper .vy -> Venom artifact JSON")
    c.add_argument("contract")
    c.add_argument("-o", "--out", default="artifacts/erc20.json")
    c.add_argument("--no-venom", action="store_true", help="disable --experimental-codegen")
    c.set_defaults(func=cmd_compile)

    for name, fn, help_ in [
        ("sites", cmd_sites, "count balanceOf keccak sites"),
        ("patch", cmd_patch, "rewrite them to ~addr"),
        ("demo", cmd_demo, "compile-time report of the rewrite"),
    ]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("artifact", nargs="?", default="artifacts/erc20.json")
        p.add_argument(
            "--slot",
            type=lambda s: int(s, 0),
            default=None,
            help="override the optimized map's slot (default: balanceOf from storageLayout)",
        )
        p.add_argument(
            "--map",
            default=None,
            help="optimize this map instead (a storageLayout name, e.g. 'names'); "
            "ONE map per contract — see the balance_patch module docstring",
        )
        if name == "patch":
            p.add_argument("-o", "--out", help="write patched runtime hex here")
        p.set_defaults(func=fn)

    ip = sub.add_parser(
        "irpatch",
        help="IR-level pass: optimize one or more maps with packSlot (multi-map)",
    )
    ip.add_argument("contract")
    ip.add_argument(
        "--map", action="append", default=[], metavar="NAME[=ID]",
        help="optimize map NAME with packing id ID (default 0); repeat for "
        "several maps, each needing a DISTINCT nonzero id",
    )
    ip.add_argument(
        "--mwmap", action="append", default=[], metavar="NAME",
        help="optimize a MULTI-WORD-value map NAME with the strideSlot scheme "
        "(struct / String / Bytes values); one such map per contract",
    )
    ip.add_argument("-o", "--out", help="write optimized runtime hex here")
    ip.set_defaults(func=cmd_irpatch)

    v = sub.add_parser("verify", help="machine-check the soundness proof (Lean)")
    v.add_argument("--dir", default=None, help="verification/ project dir")
    v.set_defaults(func=cmd_verify)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
