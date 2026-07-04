"""Command-line interface: ``venom-opt <compile|sites|patch|demo|verify>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from venom_opt import balance_patch as bp
from venom_opt.compiler import write_artifact


def _load(args: argparse.Namespace) -> tuple[bytes, int]:
    """Runtime bytecode + the balanceOf slot (override, else from storageLayout)."""
    rt = bp.runtime_from_artifact(args.artifact)
    slot = args.slot
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
            help="override balanceOf slot (default: read from storageLayout)",
        )
        if name == "patch":
            p.add_argument("-o", "--out", help="write patched runtime hex here")
        p.set_defaults(func=fn)

    v = sub.add_parser("verify", help="machine-check the soundness proof (Lean)")
    v.add_argument("--dir", default=None, help="verification/ project dir")
    v.set_defaults(func=cmd_verify)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
