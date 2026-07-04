# pragma version >=0.4.4

# Coexistence + dynamic-keyed-map probe: an address-keyed balance map next to
# a String-keyed map (two-stage slot derivation: inner variable-size keccak of
# the key bytes, then the standard 64-byte outer keccak) and a bytes32-keyed
# map (single-word key, same shape as balanceOf). The peephole optimizes ONE
# designated map per contract; the others must be left byte-identical.

balanceOf: public(HashMap[address, uint256])
names: public(HashMap[String[64], uint256])
tags: public(HashMap[bytes32, uint256])

@external
def mint(_to: address, _value: uint256):
    self.balanceOf[_to] += _value

@external
def transfer(_to: address, _value: uint256) -> bool:
    self.balanceOf[msg.sender] -= _value
    self.balanceOf[_to] += _value
    return True

@external
def set_name(_k: String[64], _v: uint256):
    self.names[_k] = _v

@external
def bump_name(_k: String[64]) -> uint256:
    self.names[_k] += 1
    return self.names[_k]

@external
def set_tag(_k: bytes32, _v: uint256):
    self.tags[_k] = _v
