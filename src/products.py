# fmt: off
from dataclasses import dataclass, field
from typing import List, Literal, Tuple

from config import cfg

# fmt: off
IPHONES = [
    "14,6", "14,5", "14,4", "14,3", "14,2", 
    "13,4", "13,3", "13,2", "13,1", "12,8", 
    "12,5", "12,3", "12,1", "11,8", "11,6", 
    "11,4", "11,2", "10,6", "10,5", "10,4", 
    "10,3", "10,2", "10,1", "9,4", "9,3", "9,2", 
    "9,1", "8,4", "8,2", "8,1", "7,2", 
    "7,1", "6,2", "6,1", "5,4", "5,3", 
    "5,2", "5,1", "4,1", "3,3", "3,2", 
    "3,1", "2,1"
]

# fmt: off
IPADS = [
    "16,6", "16,4", "16,2", "15,8", "15,6", 
    "15,5", "15,4", "15,3", "14,11", "14,9", 
    "14,6", "14,4", "14,2", "13,19", "13,17", 
    "13,11", "13,10", "13,7", "13,5", "13,2", 
    "12,2", "11,7", "11,4", "11,2", "8,12", 
    "8,10", "8,8", "8,7", "8,4", "8,3", 
    "7,12", "7,6", "7,4", "7,2", "6,12", 
    "6,8", "6,4", "5,4", "5,2", "4,9", 
    "4,8", "4,6", "4,5", "4,3", "4,2", 
    "3,6", "3,5", "3,3", "3,2", "2,7", 
    "2,6", "2,3", "2,2", "1,1"
]

@dataclass
class _Models:
    iphones: List[str] = field(default_factory=lambda: IPHONES.copy())
    ipads: List[str] = field(default_factory=lambda: IPADS.copy())

    _iter_index: int = 0
    _current_iter_model: str = "iPhone"

    def __iter__(self):
        # reset iterator state 
        self._iter_index = 0
        self._current_iter_model = "iPhone"
        return self

    def __next__(self) -> str:
        if self._current_iter_model == "iPhone":
            if self._iter_index >= len(self.iphones):
                # switch to iPad iteration
                self._iter_index = 0
                self._current_iter_model = "iPad"
                return self.__next__()

            value = "iPhone" + self.iphones[self._iter_index]
            self._iter_index += 1
            return value

        elif self._current_iter_model == "iPad":
            if self._iter_index >= len(self.ipads):
                raise StopIteration

            value = "iPad" + self.ipads[self._iter_index]
            self._iter_index += 1
            return value

        raise StopIteration



models = _Models()

def init_models() -> None:
    global models

    if cfg.product_skip > 0 and cfg.product is None:
        models.iphones = models.iphones[:-cfg.product_skip]
        models.ipads = models.ipads[:-cfg.product_skip]

    if cfg.product is not None:
        if cfg.product.startswith("iPhone"):
            models.iphones = [cfg.product.removeprefix("iPhone")]
            models.ipads = []

        elif cfg.product.startswith("iPad"):
            models.ipads = [cfg.product.removeprefix("iPad")]
            models.iphones = []
