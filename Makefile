.PHONY: build patch demo test verify check-mapping vectors lint fmt all clean

CLI = PYTHONPATH=src python3 -m venom_opt.cli

# ruff via uvx (no install needed; this repo is uv-managed, see uv.lock)
RUFF = uvx ruff
PYSRC = src tests scripts

# Vyper ERC20 -> Venom artifact (creation + runtime + storage layout)
build:
	$(CLI) compile contracts/ERC20.vy -o artifacts/erc20.json
	$(CLI) compile contracts/ERC20Dyn.vy -o artifacts/erc20dyn.json
	$(CLI) compile contracts/MixedKeys.vy -o artifacts/mixedkeys.json
	$(CLI) compile contracts/DynValue.vy -o artifacts/dynvalue.json
	$(CLI) compile contracts/TwoMaps.vy -o artifacts/twomaps.json

# report the rewrite (sites, length-preservation, changed bytes)
demo:
	$(CLI) demo artifacts/erc20.json

# write the patched runtime hex
patch:
	$(CLI) patch artifacts/erc20.json -o artifacts/erc20.patched.hex

# python unit + (boa) differential tests
test:
	PYTHONPATH=src python3 -m pytest tests -q

# regenerate the verified calldata vectors from evm-abi-lean's encoder at the
# PINNED git commit (cloned into a cache; no local checkout needed — override
# with ABI_LEAN=<path> or ABI_LEAN_REV/ABI_LEAN_GIT); test_abi_vectors.py pins
# the in-repo encoder to these bytes and the dynamic differential tests drive them
vectors:
	python3 scripts/gen_abi_vectors.py

# machine-check the soundness proof (mathlib-free Lean).
# Pinned to leanprover/lean4:v4.30.0-rc1 (verification/lean-toolchain) — elan
# fetches it automatically on first `lake build`; no mathlib, builds in seconds.
verify:
	$(CLI) verify

# guard the README end-to-end map: the referenced EVMYulLean theorems still exist
# (skips if EVMYulLean is not checked out alongside; point it with EVMYULLEAN_DIR).
# Prints the EVMYulLean commit it validated against (provenance).
check-mapping:
	@d="$${EVMYULLEAN_DIR:-../EVMYulLean}"; \
	  echo "map guard: validating against EVMYulLean @ $$(git -C "$$d" rev-parse --short HEAD 2>/dev/null || echo '<not a git checkout / not found>')"
	PYTHONPATH=src python3 -m pytest tests/test_evmyullean_mapping.py -q

# lint the Python sources (compiler, tests, vector generator)
lint:
	$(RUFF) check $(PYSRC)

# auto-format the Python sources in place
fmt:
	$(RUFF) format $(PYSRC)

all: build demo test verify

clean:
	rm -rf src/venom_opt/__pycache__ tests/__pycache__ .pytest_cache
	rm -f artifacts/erc20.patched.hex
	cd verification && lake clean || true
