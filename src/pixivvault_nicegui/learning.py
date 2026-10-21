"""学習ページ単位の状態と、差し替え可能な判定保存契約。"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .models import Artwork, RequestToken


CHOICES = frozenset(("skip", "hate", "like", "superLike"))


@dataclass(frozen=True, slots=True)
class Judgment:
    work_id: str
    choice: str
    operation_id: str


class JudgmentLedger(Protocol):
    """保存と取り消しが成功した場合に限り状態を変更する。例外は呼出側で表示する。"""

    def record(self, judgment: Judgment) -> None: ...

    def revert(self, judgment: Judgment, operation_id: str) -> None: ...


class MemoryJudgmentLedger:
    """デモ用のLIFO台帳。取り消したIDも再利用させない。"""

    def __init__(self) -> None:
        self.entries: list[Judgment] = []
        self.used_operation_ids: set[str] = set()

    def record(self, judgment: Judgment) -> None:
        if judgment.operation_id in self.used_operation_ids:
            raise ValueError("duplicate operation_id")
        self.entries.append(judgment)
        self.used_operation_ids.add(judgment.operation_id)

    def revert(self, judgment: Judgment, operation_id: str) -> None:
        if operation_id in self.used_operation_ids:
            raise ValueError("duplicate operation_id")
        if not self.entries or self.entries[-1] != judgment:
            raise ValueError("judgment is not latest")
        self.entries.pop()
        self.used_operation_ids.add(operation_id)


class LearningState:
    def __init__(self, artworks: Iterable[Artwork], ledger: JudgmentLedger | None = None) -> None:
        self.artworks = tuple(artworks)
        if len({item.work_id for item in self.artworks}) != len(self.artworks):
            raise ValueError("duplicate work_id")
        self.ledger: JudgmentLedger = ledger if ledger is not None else MemoryJudgmentLedger()
        self.index = 0
        self.generation = 0
        self.ready = False
        self.closed = False
        self.last_error: str | None = None
        self._pending: RequestToken | None = None
        self._history: list[Judgment] = []

    @property
    def current_artwork(self) -> Artwork | None:
        if self.closed or self.index >= len(self.artworks):
            return None
        return self.artworks[self.index]

    @property
    def can_undo(self) -> bool:
        return not self.closed and bool(self._history)

    def _invalidate(self) -> None:
        self.generation += 1
        self.ready = False
        self._pending = None

    def begin_load(self) -> RequestToken:
        item = self.current_artwork
        if item is None:
            raise RuntimeError("no current artwork")
        self._invalidate()
        self.last_error = None
        token = RequestToken(self.generation, item.work_id)
        self._pending = token
        return token

    def finish_load(self, token: RequestToken, success: bool = True) -> bool:
        item = self.current_artwork
        if (item is None or not isinstance(token, RequestToken)
                or token is not self._pending or token.generation != self.generation
                or token.work_id != item.work_id):
            return False
        self._pending = None
        self.ready = bool(success)
        self.last_error = None if success else "image load failed"
        return bool(success)

    def judge(self, choice: str, *, expected_work_id: str,
              expected_generation: int, operation_id: str) -> bool:
        item = self.current_artwork
        if (item is None or not self.ready or self._pending is not None
                or expected_work_id != item.work_id
                or expected_generation != self.generation):
            return False
        if (not isinstance(choice, str) or choice not in CHOICES
                or not isinstance(operation_id, str) or not operation_id):
            self.last_error = "invalid judgment"
            return False
        judgment = Judgment(item.work_id, choice, operation_id)
        try:
            self.ledger.record(judgment)
        except Exception as exc:
            self.last_error = f"judgment save failed: {exc}"
            return False
        self._history.append(judgment)
        self.index += 1
        self._invalidate()
        self.last_error = None
        return True

    def undo(self, *, operation_id: str) -> bool:
        if not self.can_undo:
            return False
        if not isinstance(operation_id, str) or not operation_id:
            self.last_error = "invalid operation_id"
            return False
        judgment = self._history[-1]
        try:
            self.ledger.revert(judgment, operation_id)
        except Exception as exc:
            self.last_error = f"undo failed: {exc}"
            return False
        self._history.pop()
        self.index -= 1
        self._invalidate()
        self.last_error = None
        return True

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._invalidate()
