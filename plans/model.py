from dataclasses import dataclass

from plans.catalog import LoopCatalog
from plans.masks import validate_bit_width


class PlanConflict(ValueError):
    """采样声明无效或与已有声明冲突。"""


@dataclass(frozen=True, slots=True)
class SamplingPlan:
    key: str
    bit_width: int
    catalog: LoopCatalog

    def __post_init__(self) -> None:
        if type(self.key) is not str or not self.key:
            raise PlanConflict('采样键必须是非空文本')
        try:
            width = validate_bit_width(self.bit_width)
        except ValueError as exc:
            raise PlanConflict(str(exc)) from exc
        if not isinstance(self.catalog, LoopCatalog) or self.catalog.bit_width != width:
            raise PlanConflict('回路表与采样位宽不一致')

    def matches_start(self, key: str, bit_width: int) -> bool:
        return self.key == key and type(bit_width) is int and self.bit_width == bit_width
