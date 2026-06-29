# verified-venom-opt

**Verified Venom optimization passes for Vyper contracts.** A generic home for
formally-verified [Venom](https://docs.vyperlang.org/en/latest/venom.html)-backend
optimizations — the **balance-slot peephole** is pass #1. Every pass ships with a
machine-checked soundness proof.

### Three facets, one project

The project goes by three names; each is a first-class part of the repo, and the
tool installs under all of them:

| facet | what it is | code | CLI |
|---|---|---|---|
| **peephole** | the optimization passes + driver | `venom_opt` / `.balance_patch` / `.compile` | `venom-peephole` |
| **balance-patch** | pass #1 — `balanceOf` keccak → `~addr` | `venom_opt.balance_patch` | `venom-balance-patch` |
| **verified-opt** | the machine-checked soundness proofs | `venom_opt.verified` → `verification/` | `verified-venom-opt` |

`venom-opt` is the umbrella command; the three above are aliases for it.

Vyper's Venom backend derives each `self.balanceOf[addr]` storage slot with a
`keccak256`. But a balance map keyed by a single address doesn't need a hash to
avoid collisions — the address *itself* is unique. This optimizer rewrites every
such slot derivation to the address's bitwise complement `~addr`, dropping a
`KECCAK256` (and its memory setup) from every balance access. The rewrite is
**length-preserving**, so the optimized runtime can be `etch`ed at the same
address.

```
 contracts/ERC20.vy
        │  vyper --experimental-codegen      (venom_opt.compiler)
        ▼
 artifacts/erc20.json   ── creation + runtime bytecode + storage layout
        │  balanceOf keccak slot  →  ~addr   (venom_opt.balance_patch)
        ▼
 optimized runtime  ── length-preserving, KECCAK256 dropped per balance access
        │  same storage as the original?     (verification/  +  EVMYulLean)
        ▼
 ✔ proved: ~ is injective ⇒ no slot aliasing ⇒ behaviour preserved
```

## Quickstart

```bash
make build         # contracts/ERC20.vy  -> artifacts/erc20.json   (vyper pinned to master)
make demo          # report the rewrite (sites / length / changed bytes)
make patch         # write the patched runtime hex
make test          # unit + titanoboa differential (mint/transfer/approve/transferFrom
                   #   parity incl. revert parity) + the EVMYulLean map guard below
make verify        # machine-check the soundness proof (mathlib-free Lean)
make check-mapping # guard the end-to-end map: the referenced EVMYulLean theorems
                   #   still exist (skips if EVMYulLean is not alongside; point it
                   #   with EVMYULLEAN_DIR)
```

`make demo` on the bundled ERC-20:

```
Balance slot  : 6
Original size : 1009
Patched size  : 1009  (length-preserving)
Balance sites : 6 keccak slot derivations -> ~addr
Changed bytes : 18  (3 per site)
```

The CLI is also installable (`pip install -e .` → `venom-opt`):

```bash
venom-opt compile contracts/ERC20.vy -o artifacts/erc20.json
venom-opt sites artifacts/erc20.json
venom-opt patch artifacts/erc20.json -o out.hex
venom-opt verify                            # machine-check the soundness proof
```

## The rewrite

Tracking `mem[0x00]` along the runtime, a keccak is a balance access iff the
hashed slot equals `balanceOf`'s storage slot — which **excludes `allowance`**
(a nested map) and everything else. Two byte shapes, each length-preserving:

| | original | patched |
|---|---|---|
| full | `60 20 52 60 40 5f 20` — `PUSH1 0x20 MSTORE PUSH1 0x40 PUSH0 KECCAK256` | `60 20 52 60 20 51 19` — `… PUSH1 0x20 MLOAD NOT` |
| short | `60 40 5f 20` — `PUSH1 0x40 PUSH0 KECCAK256` | `60 20 51 19` — `PUSH1 0x20 MLOAD NOT` |

> ⚠️ **`balanceOf`'s slot is contract-specific.** Here `name`/`symbol` are
> `String[32]` (2 slots each), so `balanceOf` lands at slot **6**, `allowance` at
> 7 — other layouts put it elsewhere. The tooling reads the slot from the
> artifact's `storageLayout` — never guess it.

## Soundness

The rewrite is sound because `~` is injective: distinct addresses never share a
slot, so no balance write can clobber another holder's. It's proved mathlib-free
in [`verification/`](verification/) (standard axioms only).
[verification/README.md](verification/README.md) has the theorems and the
end-to-end map to the EVMYulLean proofs against real `EVM.step`.

## Layout

```
contracts/ERC20.vy        the Vyper source
src/venom_opt/            the package
  compiler.py             vyper .vy -> Venom artifact
  balance_patch.py        pass #1: the peephole (patch / count_sites / patch_creation)
  erc20_abi.py            selectors / encoders for the differential harness
  verified.py             runs the Lean soundness proof
  cli.py                  venom-opt compile|sites|patch|demo|verify
tests/                    unit + (titanoboa) differential
artifacts/erc20.json      checked-in example artifact
verification/             the mathlib-free soundness proof (Lean)
```

## License

MIT — see [LICENSE](LICENSE).
