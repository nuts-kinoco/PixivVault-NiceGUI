"""Client-owned L18 header action; multiple independent roots require an explicit choice."""
from __future__ import annotations

from uuid import uuid4

from nicegui import ui

from ..desktop_integration import DesktopActionRejected, DesktopUnavailable


class SavedFolderButton:
    def __init__(self, services):
        self.services = services
        self.client = ui.context.client
        self.closed = False
        self.busy = False
        self.generation = 0
        self.choices = ()
        self.token = ''
        self.menu_token = None
        self.button = ui.button(icon='folder_open').props(
            'flat data-testid=saved-folder-open aria-label=保存フォルダを開く')
        with self.button:
            self.tooltip = ui.tooltip('保存フォルダを開く')
            self.menu = ui.menu().props('no-parent-event data-testid=saved-folder-menu')
        self.button.on('click', self._clicked, js_handler=(
            f'() => emit({{token: document.getElementById("c{self.button.id}")?.dataset.folderToken}})'))
        self.client.on_delete(self.close)
        self.refresh()

    def _rotate(self):
        self.generation += 1
        self.token = uuid4().hex
        self.button.props['data-folder-token'] = self.token
        self.button.update()
        self.menu_token = None

    def refresh(self):
        if self.closed:
            return
        self._rotate()
        self.menu.close()
        self.menu.clear()
        try:
            self.choices = self.services.saved_folder_choices()
            reason = '保存フォルダを開く' if self.choices else '保存先が未設定です。設定を確認して再起動してください。'
        except (DesktopActionRejected, DesktopUnavailable) as error:
            self.choices = ()
            reason = str(error)
        except Exception:
            self.choices = ()
            reason = '保存先を確認できません。設定を確認してください。'
        self.tooltip.set_text(reason)
        self.button.set_enabled(bool(self.choices) and not self.busy)

    def _valid(self, event, token):
        return (not self.closed and not self.busy and not self.services.closing
                and not self.services.closed and event.client is self.client
                and isinstance(event.args, dict) and event.args == {'token': token})

    async def _clicked(self, event):
        if not self._valid(event, self.token) or not self.choices:
            return
        choices = self.choices
        if len(choices) == 1:
            await self._launch(choices[0])
            return
        self._rotate()  # consume this header event, including queued double-clicks
        epoch = self.generation
        menu_token = self.menu_token = uuid4().hex
        self.menu.clear()
        with self.menu:
            ui.label('開く保存先を選択').classes('px-4 py-2')
            for choice in choices:
                button = ui.button(choice.label).props(
                    f'flat no-caps data-testid=saved-folder-choice-{choice.key}')
                button.props['data-folder-token'] = menu_token
                async def selected(event, *, value=choice, generation=epoch, token=menu_token):
                    if (not self._valid(event, token) or self.generation != generation
                            or self.menu_token != token or not self.menu.value):
                        return
                    await self._launch(value)
                button.on('click', selected, js_handler=(
                    f'() => emit({{token: document.getElementById("c{button.id}")?.dataset.folderToken}})'))
        self.menu.open()

    async def _launch(self, choice):
        self._rotate()
        epoch = self.generation
        self.busy = True
        self.menu.close()
        self.button.disable()

        def current():
            return not self.closed and self.busy and self.generation == epoch

        try:
            await self.services.open_saved_folder(choice, is_current=current)
        except (DesktopActionRejected, DesktopUnavailable) as error:
            if current():
                with self.client:
                    ui.notify(str(error), type='warning')
        except Exception:
            if current():
                with self.client:
                    ui.notify('保存先を開けませんでした。もう一度明示的に選択してください。', type='warning')
        finally:
            if current():
                self.busy = False
                self.refresh()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.generation += 1
        self.menu_token = None
        self.busy = False
