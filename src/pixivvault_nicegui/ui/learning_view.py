"""架空作品のみを使う学習デモ画面。ブラウザ送信時点の作品世代を検証する。"""

from __future__ import annotations

from uuid import uuid4

from nicegui import ui

from ..image_events import source_matches
from ..learning import LearningState, MemoryJudgmentLedger
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
        self.state = LearningState(services.artworks, self.ledger)
        self.active = False
        self._image_token = None
        self._image_url = ""
        self.client = ui.context.client
        self.client.on_delete(self.close)

        with ui.column().classes("pv-learning w-full gap-3") as self.root:
            ui.label("学習（デモ）").classes("pv-view-title")
            ui.label("判定はこの画面内のデモ台帳だけに記録されます。実データへの保存はありません。").classes("pv-subtitle")
            self.title = ui.label().classes("pv-view-title")
            self.progress = ui.label().classes("pv-counter")
            self.picture = VerifiedImage(alt="判定対象の作品画像").classes("pv-view-art")
            self.status = ui.label().classes("pv-subtitle")
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
            ui.label("矢印キー / WASD / 1〜4 で判定。画面を選択して操作してください。").classes("pv-subtitle")

        # NiceGUI 3.18: js_handler は emit(...) へ送る値を組み立てられる。
        # root 配下にフォーカスがある場合だけ拾い、入力中とIME中は除外する。
        self.root.props("tabindex=0")
        self.root.on("keydown", self._on_key, js_handler=self._key_js())
        self.picture.on("load", self._on_loaded)
        self.picture.on("error", self._on_failed)
        self.set_active(False)
        self.refresh()

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
        if not self.active or self.state.closed:
            return False
        generation = payload.get("generation")
        if type(generation) is not int:
            return False
        success = self.state.judge(
            choice, expected_work_id=payload.get("work_id"),
            expected_generation=generation, operation_id=payload.get("operation_id"),
        )
        if success:
            self.refresh()
        elif self.state.last_error:
            self.status.set_text(self.state.last_error)
        return success

    def _on_undo(self, event) -> None:
        payload = self._payload(event)
        item = self.state.current_artwork
        # Undo は直前の操作で画面が切り替わった後に届くイベントを拒否する。
        if (not self.active or self.state.closed or
                payload.get("work_id") != (item.work_id if item else "") or
                type(payload.get("generation")) is not int or
                payload["generation"] != self.state.generation):
            return
        if self.state.undo(operation_id=payload.get("operation_id")):
            self.refresh()
        elif self.state.last_error:
            self.status.set_text(self.state.last_error)

    def set_active(self, active: bool) -> None:
        self.active = bool(active) and not self.state.closed
        self.root.props(f"data-active={'true' if self.active else 'false'}")
        self.root.set_visibility(self.active)
        self._update_buttons()
        if self.active and self.client.has_socket_connection:
            self.client.run_javascript(
                f"requestAnimationFrame(() => document.getElementById('{self.root.html_id}')"
                "?.focus({preventScroll:true}))"
            )

    def _update_buttons(self) -> None:
        item = self.state.current_artwork
        work_id = item.work_id if item else ""
        props = f"data-work-id={work_id} data-generation={self.state.generation}"
        self.root.props(props)
        for button in self.buttons.values():
            button.props(props)
            button.set_enabled(self.active and self.state.ready)
        self.undo_button.props(props)
        self.undo_button.set_enabled(self.active and self.state.can_undo)
        self.retry_button.set_enabled(self.active and item is not None)

    def refresh(self) -> None:
        item = self.state.current_artwork
        self.title.set_text(item.title if item else "判定が完了しました")
        self.progress.set_text(f"{self.state.index} / {len(self.state.artworks)} 件判定済み")
        if item is None:
            self._image_token = None
            self._image_url = ""
            self.picture.set_visibility(False)
            self.status.set_text("すべての判定が完了しました。Undo で戻せます。")
        else:
            self.picture.set_visibility(True)
            self.request_image()
        self._update_buttons()

    def request_image(self) -> None:
        item = self.state.current_artwork
        if item is None or self.state.closed:
            return
        self._image_token = self.state.begin_load()
        self._image_url = (self.services.images.url(item.work_id, 0, "view")
                           + f"#learning-{self._image_token.generation}")
        self.picture.set_source(self._image_url)
        self.status.set_text("画像を読み込んでいます…")
        self._update_buttons()

    def _on_loaded(self, event) -> None:
        if self._image_token is not None and source_matches(event.args, self._image_url):
            if self.state.finish_load(self._image_token):
                self.status.set_text("")
                self._update_buttons()

    def _on_failed(self, event) -> None:
        if self._image_token is not None and source_matches(event.args, self._image_url):
            self.state.finish_load(self._image_token, success=False)
            if self.state.last_error:
                self.status.set_text("画像を読み込めませんでした。再読込してください。")
                self._update_buttons()

    def close(self) -> None:
        self.active = False
        self.state.close()
        self._image_token = None
        self._image_url = ""
