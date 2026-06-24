/-
# Soundness of the balance-slot peephole (`keccak(slot ++ addr)` → `~addr`)

The patcher rewrites every `self.balanceOf[addr]` storage-slot derivation from a
keccak hash to the address's bitwise complement, `~addr`. Replacing one slot
function with another is sound **iff the new one is injective** — otherwise two
distinct holders could collide on the same slot and a write to one would clobber
the other's balance.

This file proves exactly that, self-contained on `BitVec 256` (no mathlib):

* `optSlot` (`~·`) is **injective** — `optSlot_injective`;
* hence distinct addresses get **distinct slots** — `distinct_addresses_distinct_opt_slots`;
* hence a balance write **never aliases** another holder's slot — `write_no_alias`.

This is the mathematical heart of the rewrite. The full, end-to-end statement —
that the *rewritten EVM bytecode* computes the same storage as the original,
against EVMYulLean's real `EVM.step` — is proved in the EVMYulLean development
(`SlotAbstraction.realizes_write_opt`, `NoAlias.distinct_addresses_distinct_opt_slots`,
`venomBalanceLoad_orig_opt_equiv`). See `../README.md` for the map.
-/

namespace VenomOpt

/-- An EVM word / storage slot / address (addresses inject into 256-bit words). -/
abbrev Word := BitVec 256

/-- The optimized slot function the peephole installs: `addr ↦ ~addr`. -/
def optSlot (a : Word) : Word := ~~~a

/-- **`~` is its own inverse**, so the optimized slot function is injective. -/
theorem optSlot_injective : Function.Injective optSlot := by
  intro a b h
  -- h : ~~~a = ~~~b ; apply `~~~` to both sides and use the involution
  have := congrArg (~~~ ·) h
  simpa [optSlot] using this

/-- **No collisions.** Distinct addresses derive distinct optimized slots — the
peephole's safety condition. -/
theorem distinct_addresses_distinct_opt_slots {a b : Word} (h : a ≠ b) :
    optSlot a ≠ optSlot b :=
  fun e => h (optSlot_injective e)

/-! ## The aliasing-freedom corollary

Model storage as `Word → Word` and a balance write as an update at the holder's
optimized slot. Reading any *other* holder's balance is then unaffected. -/

/-- Write `v` to the balance slot of holder `a`. -/
def writeBal (σ : Word → Word) (a v : Word) : Word → Word :=
  fun s => if s = optSlot a then v else σ s

/-- **A balance write never disturbs a different holder's balance.** -/
theorem write_no_alias (σ : Word → Word) (a b v : Word) (h : a ≠ b) :
    writeBal σ a v (optSlot b) = σ (optSlot b) := by
  simp only [writeBal]
  rw [if_neg (distinct_addresses_distinct_opt_slots (Ne.symm h))]

/-- And a holder reads back exactly what was written to their own balance. -/
theorem read_own_write (σ : Word → Word) (a v : Word) :
    writeBal σ a v (optSlot a) = v := by
  simp [writeBal]

end VenomOpt
