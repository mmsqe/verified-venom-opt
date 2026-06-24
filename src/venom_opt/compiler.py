"""Compile a Vyper contract to the Venom artifact the patcher consumes.

Shells out to the ``vyper`` CLI with ``--experimental-codegen`` (the Venom
backend) and assembles a Foundry-style artifact:

    {"bytecode": {"object": ...}, "deployedBytecode": {"object": ...},
     "storageLayout": {...}}

`bytecode` is the creation code, `deployedBytecode` the runtime, and
`storageLayout` lets the tooling resolve balanceOf's slot per contract.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _vyper(contract: Path, fmt: str, *, venom: bool) -> str:
    args = ["vyper"]
    if venom:
        args.append("--experimental-codegen")
    args += ["-f", fmt, str(contract)]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


def compile_artifact(contract: str | Path, *, venom: bool = True) -> dict:
    """Return the artifact dict for ``contract`` (a ``.vy`` path)."""
    c = Path(contract)
    layout = json.loads(_vyper(c, "layout", venom=False))["storage_layout"]
    return {
        "contractName": c.stem,
        "bytecode": {"object": _vyper(c, "bytecode", venom=venom)},
        "deployedBytecode": {"object": _vyper(c, "bytecode_runtime", venom=venom)},
        "storageLayout": layout,
    }


def write_artifact(contract: str | Path, out: str | Path, *, venom: bool = True) -> dict:
    art = compile_artifact(contract, venom=venom)
    Path(out).write_text(json.dumps(art, indent=2) + "\n")
    return art
