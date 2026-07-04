# pragma version >=0.4.4

# The dynamic-ABI companion of ERC20.vy: the same balanceOf/allowance layout,
# plus entry points taking DynArray / Bytes / struct arguments, so the
# differential harness can exercise the peephole on calldata the primitive
# encoders cannot produce (batch transfers, byte payloads, struct args).

from ethereum.ercs import IERC20

struct Payment:
    to: address
    amount: uint256

name: public(String[32])
symbol: public(String[32])
decimals: public(uint8)
totalSupply: public(uint256)
balanceOf: public(HashMap[address, uint256])
allowance: public(HashMap[address, HashMap[address, uint256]])

@deploy
def __init__(_name: String[32], _symbol: String[32], _decimals: uint8):
    self.name = _name
    self.symbol = _symbol
    self.decimals = _decimals

@external
def mint(_to: address, _value: uint256):
    self.totalSupply += _value
    self.balanceOf[_to] += _value
    log IERC20.Transfer(sender=empty(address), receiver=_to, value=_value)

@external
def transfer(_to: address, _value: uint256) -> bool:
    self.balanceOf[msg.sender] -= _value
    self.balanceOf[_to] += _value
    log IERC20.Transfer(sender=msg.sender, receiver=_to, value=_value)
    return True

@external
def batch_transfer(_tos: DynArray[address, 8], _values: DynArray[uint256, 8]) -> bool:
    assert len(_tos) == len(_values)
    for i: uint256 in range(len(_tos), bound=8):
        self.balanceOf[msg.sender] -= _values[i]
        self.balanceOf[_tos[i]] += _values[i]
        log IERC20.Transfer(sender=msg.sender, receiver=_tos[i], value=_values[i])
    return True

@external
def transfer_with_note(_to: address, _value: uint256, _note: Bytes[64]) -> bool:
    self.balanceOf[msg.sender] -= _value
    self.balanceOf[_to] += _value
    log IERC20.Transfer(sender=msg.sender, receiver=_to, value=_value)
    return True

@external
def pay(_p: Payment) -> bool:
    self.balanceOf[msg.sender] -= _p.amount
    self.balanceOf[_p.to] += _p.amount
    log IERC20.Transfer(sender=msg.sender, receiver=_p.to, value=_p.amount)
    return True
