# pragma version >=0.4.4

# Dynamic-VALUE probe: a String[64]-valued map next to balanceOf. Multi-word
# values live at keccak-base+i, so the ~key rewrite would interleave adjacent
# keys' value windows — the tooling must REFUSE to optimize `notes`
# (map_slot_from_artifact), while patching balanceOf coexists untouched.

balanceOf: public(HashMap[address, uint256])
notes: public(HashMap[address, String[64]])

@external
def mint(_to: address, _value: uint256):
    self.balanceOf[_to] += _value

@external
def transfer(_to: address, _value: uint256) -> bool:
    self.balanceOf[msg.sender] -= _value
    self.balanceOf[_to] += _value
    return True

@external
def set_note(_k: address, _v: String[64]):
    self.notes[_k] = _v
