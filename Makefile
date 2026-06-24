.PHONY: build patch demo test verify all clean

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

# machine-check the soundness proof (mathlib-free Lean)
verify:
	$(CLI) verify

all: build demo test verify

clean:
	rm -rf src/venom_opt/__pycache__ tests/__pycache__ .pytest_cache
	rm -f artifacts/erc20.patched.hex
	cd verification && lake clean || true
