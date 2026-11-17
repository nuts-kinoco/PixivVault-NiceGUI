"""メタデータ後埋めの自動実行ループ（元 scheduler.py:104-123 相当）。

このモジュールはスレッドとタイミングだけを持つ。通信・台帳・停止ゲートは
呼び出し側の ``run_pass`` が責任を持つ（metadata_runner.py の「スレッドを持たない」
契約はそのまま保つ）。通信の暴走を防ぐため、

* 設定が OFF のあいだは ``run_pass`` を一度も呼ばない。
* 1 周の件数上限・停止ゲート・ダウンロード優先は ``run_pass`` 側で守る。
* 認証や全体障害で止まったら 15 分から倍々（最大 6 時間）で間隔を空ける。
* 停止要求には待ち時間を即座に切り上げて応じる。
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Callable

from .metadata_runner import MetadataRunSummary

DEFAULT_INTERVALS = {
    'idle': 1800.0,          # 対象が無い、または一巡して残りが無い
    'after_work': 300.0,     # 件数上限で打ち切った。続きを少し空けて見る
    'busy': 60.0,            # ダウンロード中など。急がない
    'stopped': 300.0,        # 取得停止ゲートが閉じている
    'backoff_start': 900.0,  # 認証・全体障害の最初の待ち
    'backoff_max': 21600.0,
}
_BACKOFF_REASONS = frozenset((
    'global_failure', 'authentication_unverified', 'authentication_or_state_unavailable',
    'five_consecutive_unknown_failures',
))


def next_delay(summary: MetadataRunSummary, backoff: float,
               intervals: dict | None = None) -> tuple[float, float]:
    """(次の周までの秒数, 新しい backoff)。失敗の連続は倍々、成功で 0 に戻す。"""
    iv = {**DEFAULT_INTERVALS, **(intervals or {})}
    reason = summary.stopped
    if reason in _BACKOFF_REASONS:
        backoff = iv['backoff_start'] if not backoff else min(backoff * 2, iv['backoff_max'])
        return backoff, backoff
    if reason == 'download_busy':
        return iv['busy'], backoff
    if reason == 'network_stopped':
        return iv['stopped'], backoff
    if reason:
        return iv['busy'], backoff
    return (iv['after_work'] if summary.target > 0 else iv['idle']), 0


@dataclass(frozen=True, slots=True)
class MetadataStatus:
    """「メタデータ: N / M 件（残り R …）」の素。数は保存済み作品の件数（元 count_meta_status）。"""
    filled: int = 0
    total: int = 0
    remaining: int = 0
    gave_up: int = 0
    blocked: int = 0

    @property
    def text(self) -> str:
        # 「残り」を必ず出す。終わったのか止まったのかが画面から分かるように（元と同じ）。
        tail = f' ／ 3回試しても取れなかった {self.gave_up}' if self.gave_up else ''
        if self.blocked:
            tail += f' ／ 通信できず保留 {self.blocked}'
        return f'メタデータ: {self.filled} / {self.total} 件（残り {self.remaining}{tail}）'


class FencedTransport:
    """1 回の GET ごとに新規ダウンロード受付の鍵を取る（一巡のあいだ握り続けない）。"""

    def __init__(self, inner, lock):
        self._inner = inner
        self._lock = lock

    def verify_identity(self):
        with self._lock:
            return self._inner.verify_identity()

    def fetch_detail(self, work_id, expected_author_id):
        with self._lock:
            return self._inner.fetch_detail(work_id, expected_author_id)


class MetadataAutoRunner:
    """デーモンスレッド 1 本。start() は何度呼んでも 2 本目を立てない。"""

    def __init__(self, run_pass: Callable[[], MetadataRunSummary],
                 enabled: Callable[[], bool], *, initial_delay: float = 60.0,
                 intervals: dict | None = None):
        if not callable(run_pass) or not callable(enabled):
            raise TypeError('callables required')
        self._run_pass = run_pass
        self._enabled = enabled
        self._initial = float(initial_delay)
        self._intervals = intervals
        self._lock = Lock()
        self._thread: Thread | None = None
        self._stop = Event()
        self._wake = Event()
        self._backoff = 0.0

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop.is_set()

    def start(self) -> None:
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError('metadata auto runner already stopped')
            if self._thread is not None:
                return
            self._thread = Thread(target=self._loop, name='pixivvault-metadata-auto', daemon=True)
            self._thread.start()

    def wake(self) -> None:
        """設定の変更を待たずに、次の判断をすぐやり直させる。"""
        self._wake.set()

    def stop(self, timeout: float = 150.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise TimeoutError('metadata auto runner shutdown not confirmed')

    def _sleep(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _loop(self) -> None:
        self._sleep(self._initial)
        while not self._stop.is_set():
            try:
                if not self._enabled():
                    # OFF のあいだは何も呼ばない。wake() か次の確認まで眠る。
                    self._sleep(DEFAULT_INTERVALS['busy'] if not self._intervals
                                else {**DEFAULT_INTERVALS, **self._intervals}['busy'])
                    continue
                summary = self._run_pass()
            except Exception:
                summary = MetadataRunSummary(stopped='global_failure')
            if self._stop.is_set():
                return
            delay, self._backoff = next_delay(summary, self._backoff, self._intervals)
            self._sleep(delay)


__all__ = ['MetadataAutoRunner', 'MetadataStatus', 'FencedTransport', 'next_delay',
           'DEFAULT_INTERVALS']
