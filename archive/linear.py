from collections.abc import Iterable


Equation = tuple[int, int]


class XorBasis:
    """以最高置位作为主元的二元异或方程组。"""

    def __init__(self) -> None:
        self._pivots: dict[int, Equation] = {}

    def add(self, mask: int, value: int) -> bool:
        """尝试追加一行。矛盾时返回 False 并保留原有基。"""
        if type(mask) is not int or mask < 0:
            raise ValueError('线性方程的掩码必须是非负整数')
        if type(value) is not int or value not in (0, 1):
            raise ValueError('线性方程的值必须是整数 0 或 1')
        while mask:
            pivot = mask.bit_length() - 1
            row = self._pivots.get(pivot)
            if row is None:
                self._pivots[pivot] = (mask, value)
                return True
            row_mask, row_value = row
            mask ^= row_mask
            value ^= row_value
        return value == 0


def equations_consistent(equations: Iterable[Equation]) -> bool:
    basis = XorBasis()
    return all(basis.add(mask, value) for mask, value in equations)
