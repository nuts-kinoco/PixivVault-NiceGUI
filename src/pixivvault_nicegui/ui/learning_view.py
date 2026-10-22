"""架空作品のみを使う学習デモ画面。ブラウザ送信時点の作品世代を検証する。"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future

from nicegui import background_tasks, ui

from ..image_events import source_matches
from ..learning import LearningState, MemoryJudgmentLedger, SaveCommand
from ..services import ApplicationServices
from .verified_image import VerifiedImage


class LearningView:
    CHOICES = (
        ("見送る", "skip", "arrowleft", "a", "1"),
        ("合わない", "hate", "arrowdown", "s", "2"),
        ("いいね", "like", "arrowright", "d", "3"),
        ("SuperLike", "superLike", "arrowup", "w", "4"),
    )

    def __init__(self, services: ApplicationServices) -> None:
        self.services = services
        self.ledger = MemoryJudgmentLedger()
        self.storage = services.learning
        self.state = LearningState(services.artworks if self.storage is None else (), self.ledger)
        self._session_id: str | None = None
        self._storage_busy = False
        self._storage_ready = self.storage is None
        self._baseline_count = 0
        self._failed_command: SaveCommand | None = None
        self._notice = ""
        self._save_task = None
        self.active = False
        self._image_token = None
        self._image_url = ""
        self.client = ui.context.client
        self.client.on_delete(self.close)

        with ui.column().classes("pv-learning w-full gap-3") as self.root:
            ui.label("学習（デモ）").classes("pv-view-title")
            description = (
                "判定はデモ専用フォルダーに保存します。Undoはこの画面での直前の判定だけが対象です。"
                if self.storage is not None else
                "判定はこの画面内のデモ台帳だけに記録されます。実データへの保存はありません。"
            )
            ui.label(description).classes("pv-subtitle")
            self.title = ui.label().classes("pv-view-title")
            self.progress = ui.label().classes("pv-counter")
            self.picture = VerifiedImage(alt="判定対象の作品画像").classes("pv-view-art")
            self.status = ui.label().classes("pv-subtitle pv-learning-status").props("role=status aria-live=polite")
            with ui.row().classes("items-center gap-2"):
                self.buttons = {}
                for label, choice, *_ in self.CHOICES:
                    button = ui.button(label, color=None).props(
                        "no-caps" if choice == "like" else "outline no-caps"
                    ).classes(f"pv-judgment pv-judgment-{choice}")
                    button.on("click", lambda event, c=choice: self._on_judgment(c, event),
                              js_handler=self._click_js())
                    self.buttons[choice] = button
            with ui.row().classes("items-center gap-2"):
                self.undo_button = ui.button("ひとつ戻す（Undo）", color=None).props("flat no-caps").classes("pv-undo")
                self.undo_button.on("click", self._on_undo, js_handler=self._click_js())
                self.retry_button = ui.button("画像を再読込", on_click=self.request_image).props("flat")
                self.save_retry_button = ui.button("保存を再試行", on_click=self.retry_save).props("flat")
                self.reload_button = ui.button("保存状態を再読込", on_click=self.reload_storage).props("flat")
            ui.label("矢印キー / WASD / 1〜4 で判定。画面を選択して操作してください。").classes("pv-subtitle")

        # NiceGUI 3.18: js_handler は emit(...) へ送る値を組み立てられる。
        # root 配下にフォーカスがある場合だけ拾い、入力中とIME中は除外する。
        self.root.props(f"tabindex=0 data-storage-mode={'sandbox' if self.storage is not None else 'memory'}")
        self.root.on("keydown", self._on_key, js_handler=self._key_js())
        self.picture.on("load", self._on_loaded)
        self.picture.on("error", self._on_failed)
        self.set_active(False)
        if self.storage is None:
            self.refresh()
        else:
            self.picture.set_visibility(False)
            self.reload_storage()

    @staticmethod
    def _click_js() -> str:
        return """(event) => {
            const el = event.currentTarget;
            emit({work_id: el.getAttribute('data-work-id'),
                  generation: Number(el.getAttribute('data-generation')),
                  operation_id: crypto.randomUUID()});
        }"""

    @staticmethod
    def _key_js() -> str:
        return """(event) => {
            const tag = event.target?.tagName?.toLowerCase();
            if (event.repeat || event.isComposing || event.keyCode === 229 ||
                event.ctrlKey || event.altKey || event.metaKey ||
                ['input', 'textarea', 'select'].includes(tag) ||
                event.target?.isContentEditable ||
                event.target?.closest('[role=dialog]') ||
                !event.currentTarget.getAttribute('data-active') ||
                event.currentTarget.getAttribute('data-active') !== 'true') return;
            const map = {arrowleft:'skip',a:'skip','1':'skip',
                         arrowdown:'hate',s:'hate','2':'hate',
                         arrowright:'like',d:'like','3':'like',
                         arrowup:'superLike',w:'superLike','4':'superLike'};
            const choice = map[event.key.toLowerCase()];
            if (!choice) return;
            event.preventDefault();
            emit({choice, work_id:event.currentTarget.getAttribute('data-work-id'),
                  generation:Number(event.currentTarget.getAttribute('data-generation')),
                  operation_id:crypto.randomUUID()});
        }"""

    @staticmethod
    def _payload(event) -> dict:
        return event.args if isinstance(event.args, dict) else {}

    def _on_judgment(self, choice: str, event) -> None:
        self.judge(choice, self._payload(event))

    def _on_key(self, event) -> None:
        data = self._payload(event)
        self.judge(data.get("choice"), data)

    def judge(self, choice: str, payload: dict) -> bool:
        if not self.active or self.state.closed or self.services.closing or not self._storage_ready or self._storage_busy:
            return False
        generation = payload.get("generation")
        if type(generation) is not int:
            return False
        options = dict(expected_work_id=payload.get("work_id"),
                       expected_generation=generation, operation_id=payload.get("operation_id"))
        if self.storage is not None:
            command = self.state.prepare_judgment(choice, **options)
            if command is None:
                return False
            return self._start_save(command)
        success = self.state.judge(choice, **options)
        if success:
            self.refresh()
        elif self.state.last_error:
            self.status.set_text(self.state.last_error)
        return success

    def _on_undo(self, event) -> None:
        payload = self._payload(event)
        item = self.state.current_artwork
        # Undo は直前の操作で画面が切り替わった後に届くイベントを拒否する。
        if (not self.active or self.state.closed or self.services.closing or not self._storage_ready or self._storage_busy or
                payload.get("work_id") != (item.work_id if item else "") or
                type(payload.get("generation")) is not int or
                payload["generation"] != self.state.generation):
            return
        if self.storage is not None:
            command = self.state.prepare_undo(operation_id=payload.get("operation_id"))
            if command is not None:
                self._start_save(command)
            return
        if self.state.undo(operation_id=payload.get("operation_id")):
            self.refresh()
        elif self.state.last_error:
            self.status.set_text(self.state.last_error)

    def _start_save(self, command: SaveCommand) -> bool:
        # Enqueue before acknowledging the click: shutdown may cancel a new async
        # task before it ever starts. The application owns this accepted Future.
        try:
            if command.undo:
                pending = self.storage.submit_revert(self._session_id, command.judgment, command.operation_id)
            else:
                pending = self.storage.submit_record(self._session_id, command.judgment)
        except Exception as exc:
            self._save_failed(command, exc)
            return False
        self._failed_command = None
        self._notice = ""
        self.status.set_text("取消を保存しています…" if command.undo else "判定を保存しています…")
        self._update_buttons()
        self._save_task = background_tasks.create(self._persist(command, pending), name="learning save")
        return True

    async def _persist(self, command: SaveCommand, pending: Future) -> None:
        try:
            # Cancelling a UI waiter must not cancel an accepted executor job.
            wrapped = asyncio.wrap_future(pending)
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            await asyncio.shield(wrapped)
        except Exception as exc:
            self._save_failed(command, exc)
        else:
            if self.services.closing:
                self.close()
            elif self.state.complete_save(command):
                self.refresh()

    def _save_failed(self, command: SaveCommand, exc: Exception) -> None:
        from ..learning_repository import LearningConflict

        if self.services.closing:
            self.close()
        if self.state.closed or not self.state.saving:
            return
        self.state.complete_save(command, exc)
        if isinstance(exc, LearningConflict):
            self.reload_storage(
                notice="別の画面で判定が更新されたため、取消は行いませんでした。保存状態を読み直しました。"
            )
        else:
            self._failed_command = command
            self.status.set_text(self._resting_message())
            self._update_buttons()

    def _resting_message(self) -> str:
        if self._failed_command is not None:
            if self._failed_command.undo:
                return "取消を保存できませんでした。判定はそのままです。「保存を再試行」でやり直せます。"
            return "判定を保存できませんでした。画面は進めていません。「保存を再試行」でやり直せます。"
        return self._notice

    def retry_save(self) -> None:
        failed = self._failed_command
        if (failed is None or not self.active or self.state.closed or self.services.closing or
                not self._storage_ready or self._storage_busy):
            return
        if failed.undo:
            command = self.state.prepare_undo(operation_id=failed.operation_id)
        else:
            command = self.state.prepare_judgment(
                failed.judgment.choice, expected_work_id=failed.judgment.work_id,
                expected_generation=self.state.generation, operation_id=failed.operation_id,
            )
        if command is not None:
            self._start_save(command)

    def reload_storage(self, *, notice: str = "") -> None:
        if self.storage is None or self.state.closed or self.services.closing or self.state.saving or self._storage_busy:
            return
        self._storage_busy = True
        self._storage_ready = False
        self.status.set_text("保存された判定を読み込んでいます…")
        self._update_buttons()
        background_tasks.create(self._reload_storage(notice), name="learning restore")

    async def _reload_storage(self, notice: str) -> None:
        try:
            snapshot = await self.storage.open_session()
        except Exception:
            if self.services.closing:
                self.close()
            elif not self.state.closed:
                self._storage_busy = False
                self.status.set_text("保存状態を読み込めませんでした。「保存状態を再読込」でやり直してください。")
                self._update_buttons()
            return
        if self.services.closing:
            self.close()
        if self.state.closed:
            self.storage.release_session(snapshot.session_id)
            return
        if self._session_id is not None:
            self.storage.release_session(self._session_id)
        self._session_id = snapshot.session_id
        generation = self.state.generation + 1
        self.state.close()
        remaining = tuple(item for item in self.services.artworks if item.work_id not in snapshot.judged_ids)
        self._baseline_count = len(self.services.artworks) - len(remaining)
        self.state = LearningState(remaining, self.ledger)
        # A late browser event from the previous snapshot must never match again.
        self.state.generation = generation
        self._storage_busy = False
        self._storage_ready = True
        self._failed_command = None
        self._notice = notice
        self.refresh()

    def set_active(self, active: bool) -> None:
        self.active = bool(active) and not self.state.closed
        self.root.props(f"data-active={'true' if self.active else 'false'}")
        self.root.set_visibility(self.active)
        self._update_buttons()
        if self.active and self.client.has_socket_connection:
            self.client.run_javascript(
                f"""requestAnimationFrame(() => {{
                    const node = document.getElementById('{self.root.html_id}');
                    if (node?.isConnected && node.getAttribute('data-active') === 'true' &&
                        node.getClientRects().length &&
                        !node.closest('[inert], [aria-hidden="true"]'))
                        node.focus({{preventScroll:true}});
                }})"""
            )

    def _update_buttons(self) -> None:
        item = self.state.current_artwork
        work_id = item.work_id if item else ""
        props = f"data-work-id={work_id} data-generation={self.state.generation}"
        available = self.active and not self.services.closing and self._storage_ready and not self._storage_busy and not self.state.saving
        self.root.props(props + f" data-saving={'true' if self.state.saving else 'false'}")
        for button in self.buttons.values():
            button.props(props)
            button.set_enabled(available and self.state.ready)
        self.undo_button.props(props)
        self.undo_button.set_enabled(available and self.state.can_undo)
        self.retry_button.set_enabled(available and item is not None)
        self.save_retry_button.set_visibility(self._failed_command is not None)
        self.save_retry_button.set_enabled(available and (self.state.ready or self.state.can_undo))
        self.reload_button.set_visibility(self.storage is not None)
        self.reload_button.set_enabled(self.active and not self.state.closed and not self.services.closing and not self._storage_busy and not self.state.saving)

    def refresh(self) -> None:
        item = self.state.current_artwork
        self.title.set_text(item.title if item else "判定が完了しました")
        self.progress.set_text(f"{self._baseline_count + self.state.index} / {len(self.services.artworks)} 件判定済み")
        if item is None:
            self._image_token = None
            self._image_url = ""
            self.picture.set_visibility(False)
            message = "すべての判定が完了しました。"
            if self.state.can_undo:
                message += "Undo で戻せます。"
            self.status.set_text(self._notice or message)
        else:
            self.picture.set_visibility(True)
            self.request_image()
        self._update_buttons()

    def request_image(self) -> None:
        item = self.state.current_artwork
        if item is None or self.state.closed or self.services.closing or self.state.saving or not self._storage_ready or self._storage_busy:
            return
        self._image_token = self.state.begin_load()
        self._image_url = (self.services.images.url(item.work_id, 0, "view")
                           + f"#learning-{self._image_token.generation}")
        self.picture.set_source(self._image_url)
        self.status.set_text("画像を読み込んでいます…")
        self._update_buttons()

    def _on_loaded(self, event) -> None:
        if not self._storage_ready or self._storage_busy or self.state.closed or self.services.closing:
            return
        if self._image_token is not None and source_matches(event.args, self._image_url):
            if self.state.finish_load(self._image_token):
                if not self.state.saving and not self._storage_busy:
                    self.status.set_text(self._resting_message())
                self._update_buttons()

    def _on_failed(self, event) -> None:
        if not self._storage_ready or self._storage_busy or self.state.closed or self.services.closing:
            return
        if self._image_token is not None and source_matches(event.args, self._image_url):
            self.state.finish_load(self._image_token, success=False)
            if self.state.last_error:
                self.status.set_text("画像を読み込めませんでした。再読込してください。")
                self._update_buttons()

    def close(self) -> None:
        if self.state.closed:
            return
        self.active = False
        self.state.close()
        self._image_token = None
        self._image_url = ""
        if self._session_id is not None:
            # submit_* already entered the same FIFO queue before judge returned.
            # Session release is therefore after its writes, even if the waiter
            # has not started or is cancelled by application shutdown.
            self.storage.release_session(self._session_id)
            self._session_id = None
