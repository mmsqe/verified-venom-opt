# Verification

The peephole replaces the `balanceOf` slot function — a keccak hash — with the
bitwise complement `~addr`. That is sound **iff the replacement is injective**;
the theorems below establish it.

## In this repo (self-contained, mathlib-free)

[`VenomOpt/Peephole.lean`](VenomOpt/Peephole.lean) proves the mathematical core
on `BitVec 256` (standard axioms only):

| theorem | statement |
|---|---|
| `optSlot_injective` | `~·` is injective (it is its own inverse) |
| `distinct_addresses_distinct_opt_slots` | `a ≠ b → ~a ≠ ~b` — no slot collisions |
| `write_no_alias` | writing holder `a`'s balance never changes holder `b`'s (`b ≠ a`) |
| `read_own_write` | a holder reads back exactly what was written to their slot |

Build & check:

```bash
cd verification && lake build      # builds in seconds — no mathlib
```

## The full, end-to-end statement (EVMYulLean)

The proof here is the *slot-function* argument. That the **rewritten EVM
bytecode** computes the same storage as the original — against EVMYulLean's real
`EVM.step` — is proved in the EVMYulLean development:

| EVMYulLean result | what it adds |
|---|---|
| `NoAlias.distinct_addresses_distinct_opt_slots` | the same injectivity, on `UInt256.lnot` |
| `SlotAbstraction.realizes_write_opt` | the `~addr` storage scheme realizes balance write-through **unconditionally** |
| `SlotAbstraction.write_opt_preserves_named` | a `~addr` write never disturbs a named low slot (`totalSupply`, …) |
| `BalanceSlot.venomBalanceLoad_orig_opt_equiv` | original (keccak) and optimized (`~addr`) loads agree under the storage relation |
| `Solvency.transfer_preserves_solvent` / `Erc20.*` | the rewrite preserves `Σ balances = totalSupply` and the ERC-20 spec |
