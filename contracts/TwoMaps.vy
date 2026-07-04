# pragma version >=0.4.4

# Two address-keyed maps optimized in ONE contract — the case the
# length-preserving bytecode patcher cannot do (~key erases the slot, so both
# maps would collide at the same key: two_fullword_maps_must_alias). The
# IR-level pass installs packSlot with distinct ids (~(id*2^160 + key)), which
# is jointly injective (packSlot_injective / packSlot_cross_map). The decisive
# test probes the SAME key in both maps and checks they never interfere.

balances: public(HashMap[address, uint256])
bonuses: public(HashMap[address, uint256])

@external
def set_balance(_k: address, _v: uint256):
    self.balances[_k] = _v

@external
def set_bonus(_k: address, _v: uint256):
    self.bonuses[_k] = _v

@external
def add_both(_k: address, _v: uint256):
    self.balances[_k] += _v
    self.bonuses[_k] += _v
