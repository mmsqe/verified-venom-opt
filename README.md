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
make build         # contracts/*.vy -> artifacts/*.json             (vyper pinned to master)
make demo          # report the rewrite (sites / length / changed bytes)
make patch         # write the patched runtime hex
make test          # unit + titanoboa differential (static + dynamic-calldata
                   #   parity incl. revert parity) + the map guard below
make vectors       # regenerate the verified calldata vectors from the PINNED
                   #   evm-abi-lean commit (cached clone; ABI_LEAN overrides)
make verify        # machine-check the soundness proof (mathlib-free Lean)
make check-mapping # guard the end-to-end map: the referenced EVMYulLean /
                   #   evm-abi-lean theorems still exist (skips without the
                   #   siblings; point with EVMYULLEAN_DIR / EVMABILEAN_DIR)
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

Venom derives a mapping slot as `keccak256(mem[o..o+0x40))` — map slot at the
frame base `o`, key at `o+0x20`. Straight-line code uses `o = 0`; loop bodies
(e.g. a `DynArray` batch transfer) place the same idiom at other constant
offsets. Tracking *constant memory words* along the runtime — conservatively:
constant stores overwrite/forget what they overlap, and unmodeled memory
writes (copies, computed-offset stores, calls) or a `JUMPDEST` block boundary
wipe the tracking — a keccak is a balance access iff the tracked frame base
holds `balanceOf`'s storage slot, which **excludes `allowance`** (a nested
map) and everything else. Two keccak tails, each rewritten length- and
stack-effect-preserving to an MLOAD of the very key word the keccak would
have hashed:

| frame | original | patched |
|---|---|---|
| at 0 | `60 40 5f 20` — `PUSH1 0x40 PUSH0 KECCAK256` | `60 20 51 19` — `PUSH1 0x20 MLOAD NOT` |
| at `o` | `60 40 60 o 20` — `PUSH1 0x40 PUSH1 o KECCAK256` | `61 00 (o+20) 51 19` — `PUSH2 o+0x20 MLOAD NOT` |

> ⚠️ **`balanceOf`'s slot is contract-specific.** Here `name`/`symbol` are
> `String[32]` (2 slots each), so `balanceOf` lands at slot **6**, `allowance` at
> 7 — other layouts put it elsewhere. The tooling reads the slot from the
> artifact's `storageLayout` — never guess it.

## Soundness

The rewrite is sound because `~` is injective: distinct addresses never share a
slot, so no balance write can clobber another holder's. Injectivity alone is
not enough, though — a **partial** rewrite (some sites moved to `~key`, one
missed on the keccak slot) splits the map across two slot functions and reads
miss writes (`unpatched_read_misses_patched_write`); the differential tests
are the completeness oracle. Both facts are proved mathlib-free in
[`verification/`](verification/) (standard axioms only).

At the ABI level, EVMYulLean's `abi_balanceOf_orig_opt_returndata_eq`
composes the pillars end-to-end: a `balanceOf(a)` **call** — raw calldata in,
ABI-encoded returndata out — halts with identical returndata on the original
and the patched dispatcher under the per-address storage relation.
[verification/README.md](verification/README.md) has the theorems and the
end-to-end map to the EVMYulLean / evm-abi-lean proofs, drift-guarded by
`make check-mapping`.

## Dynamic-ABI coverage

`ERC20Dyn.vy` adds `DynArray` batch transfers, a `Bytes[64]` note, and a
struct argument on the same balance layout. Its differential tests are driven
by **verified calldata**: `make vectors` regenerates
`tests/vectors/abi_lean_vectors.json` out-of-process from the
[evm-abi-lean](https://github.com/yihuang/evm-abi-lean) encoder
(`functionSelector` + `encodeArgs`, roundtrip-proved by `roundtrip_args_wff`)
at a **pinned git commit** — cloned into a cache on first use, so no local
checkout is required (override with `ABI_LEAN=<path>` or
`ABI_LEAN_REV`/`ABI_LEAN_GIT`); the JSON records the pin as provenance.
`tests/test_abi_vectors.py` pins the in-repo encoder (`venom_opt.abi`, itself
cross-checked against `eth_abi`) to those bytes.

## Layout

```
contracts/ERC20.vy        the Vyper source
contracts/ERC20Dyn.vy     the dynamic-ABI companion (DynArray / Bytes / struct entry points)
src/venom_opt/            the package
  compiler.py             vyper .vy -> Venom artifact
  balance_patch.py        pass #1: the peephole (patch / count_sites / patch_creation)
  erc20_abi.py            selectors / primitive encoders for the differential harness
  abi.py                  general ABI encoder (dynamic bytes/arrays/tuples, head/tail)
  verified.py             runs the Lean soundness proof
  cli.py                  venom-opt compile|sites|patch|demo|verify
scripts/gen_abi_vectors.py  regenerate the verified calldata vectors (make vectors)
tests/                    unit + (titanoboa) differential, incl. dynamic-calldata parity
tests/vectors/            evm-abi-lean-generated calldata (with provenance commit)
artifacts/                checked-in example artifacts (erc20.json, erc20dyn.json)
verification/             the mathlib-free soundness proof (Lean)
```

## License

MIT — see [LICENSE](LICENSE).
