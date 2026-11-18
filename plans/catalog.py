from collections.abc import Mapping
from dataclasses import dataclass

from plans.masks import validate_bit_width, validate_mask


@dataclass(frozen=True, slots=True)
class LoopCatalog:
    bit_width: int
    _entries: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        width = validate_bit_width(self.bit_width)
        by_label = {}
        for label, mask in self._entries:
            if type(label) is not str or not label:
                raise ValueError('回路标签必须是非空文本')
            if label in by_label:
                raise ValueError(f'重复的回路标签 {label!r}')
            by_label[label] = validate_mask(mask, width)
        object.__setattr__(self, '_entries', tuple(sorted(by_label.items())))

    @classmethod
    def from_mapping(cls, bit_width: int, loops: Mapping[str, int]) -> 'LoopCatalog':
        if not isinstance(loops, Mapping):
            raise ValueError('回路表必须是从标签到掩码的映射')
        return cls(bit_width, tuple(loops.items()))

    def get_mask(self, label: str) -> int:
        for known_label, mask in self._entries:
            if label == known_label:
                return mask
        raise KeyError(label)

    def items(self) -> tuple[tuple[str, int], ...]:
        return self._entries
