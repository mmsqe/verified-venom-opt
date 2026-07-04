"""venom-opt — verified Venom optimization passes for Vyper contracts.

A generic home for *verified* Venom-backend optimizations; the balance-slot
peephole is the first pass. Three facets (the names the project also goes by),
each a first-class part:

* **peephole** — the optimization passes. :mod:`venom_opt.balance_patch` is pass
  #1 (the length-preserving ``balanceOf`` keccak → ``~addr`` rewrite: ``patch`` /
  ``count_sites`` / ``patch_creation``), with :mod:`venom_opt.compiler` driving
  Vyper ``.vy`` → Venom artifacts.
* **balance-patch** — the concrete balance-slot pass (``venom_opt.balance_patch``).
* **verified-opt** — :mod:`venom_opt.verified`: runs the machine-checked
  soundness proof in ``verification/`` (``verify()``).

:mod:`venom_opt.erc20_abi` provides selectors/primitive encoders and
:mod:`venom_opt.abi` the general head/tail encoder (dynamic bytes / arrays /
tuples) for the differential harness. The tool installs as ``venom-opt``
(umbrella) with ``venom-peephole`` / ``venom-balance-patch`` /
``verified-venom-opt`` as facet aliases.
"""

from venom_opt import abi, balance_patch, compiler, erc20_abi, verified

__all__ = ["abi", "balance_patch", "compiler", "erc20_abi", "verified"]
__version__ = "0.1.0"
