.PHONY: build patch demo test verify check-mapping all clean

CLI = PYTHONPATH=src python3 -m venom_opt.cli

# Vyper ERC20 -> Venom artifact (creation + runtime + storage layout)
build:
	$(CLI) compile contracts/ERC20.vy -o artifacts/erc20.json

# report the rewrite (sites, length-preservation, changed bytes)
demo:
	$(CLI) demo artifacts/erc20.json

# write the patched runtime hex
patch:
	$(CLI) patch artifacts/erc20.json -o artifacts/erc20.patched.hex

# python unit + (boa) differential tests
test:
	PYTHONPATH=src python3 -m pytest tests -q

# machine-check the soundness proof (mathlib-free Lean).
# Pinned to leanprover/lean4:v4.30.0-rc1 (verification/lean-toolchain) — elan
# fetches it automatically on first `lake build`; no mathlib, builds in seconds.
verify:
	$(CLI) verify

# guard the README end-to-end map: the referenced EVMYulLean theorems still exist
# (skips if EVMYulLean is not checked out alongside; point it with EVMYULLEAN_DIR)
check-mapping:
	PYTHONPATH=src python3 -m pytest tests/test_evmyullean_mapping.py -q

all: build demo test verify

clean:
	rm -rf src/venom_opt/__pycache__ tests/__pycache__ .pytest_cache
	rm -f artifacts/erc20.patched.hex
	cd verification && lake clean || true
