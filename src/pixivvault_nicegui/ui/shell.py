"""ローカルの架空作品だけを扱うランキングデモUI。"""

from __future__ import annotations

from nicegui import ui

from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.state import GalleryState
from .theme import CSS, TOKENS


def build_demo_page() -> None:
    """呼出し元の @ui.page('/') の各クライアントに独立状態を構築する。"""
    state = GalleryState(generate_demo_artworks(120))
    ui.context.client.on_delete(state.close)
    ui.add_css(CSS)

    viewer_elements: dict[str, object] = {}
    size_min = {"small": 145, "medium": 190, "large": 255}

    def artwork_ratio(artwork: object) -> float:
        return max(0.4, min(2.5, float(getattr(artwork, "aspect_ratio", 1.0))))

    def update_viewer() -> None:
        art = state.selected_artwork
        if art is None:
            viewer.close()
            return
        viewer_elements["art"].style(f"--pv-art-ratio:{artwork_ratio(art)}")
        viewer_elements["title"].set_text(art.title)
        viewer_elements["author"].set_text(art.author)
        viewer_elements["description"].set_text(art.description)
        viewer_elements["position"].set_text(f"{(state.viewer_index or 0) + 1} / {state.total_count}")
        index = state.viewer_index
        (viewer_elements["previous"].enable if index is not None and index > 0
         else viewer_elements["previous"].disable)()
        (viewer_elements["next"].enable if index is not None and index + 1 < state.total_count
         else viewer_elements["next"].disable)()
        if state.viewer_open:
            viewer.open()
        else:
            viewer.close()

    def show_artwork(work_id: str) -> None:
        state.open_artwork(work_id)
        update_viewer()

    def shift_viewer(delta: int) -> None:
        state.move_viewer(delta)
        update_viewer()

    def close_viewer() -> None:
        state.close_viewer()
        viewer.close()

    def render_gallery() -> None:
        gallery.clear()
        with gallery:
            artworks = state.visible_items
            if not artworks:
                ui.label("該当するデモ作品がありません").classes("pv-empty")
            else:
                with ui.element("div").classes("pv-grid").style(
                    f"--pv-card-min:{size_min.get(str(state.card_size), 190)}px"
                ):
                    for artwork in artworks:
                        with ui.element("div").classes("pv-card").on(
                            "click", lambda _, work_id=artwork.work_id: show_artwork(work_id)
                        ):
                            with ui.element("div").classes("pv-art").style(
                                f"--pv-art-ratio:{artwork_ratio(artwork)}"
                            ):
                                ui.label("デモ画像")
                            with ui.element("div").classes("pv-card-meta"):
                                ui.label(artwork.title).classes("pv-card-title")
                                ui.label(artwork.author).classes("pv-card-author")
            ui.label(f"{state.shown_count} / {state.total_count} 件表示").classes("pv-counter")
            if state.has_more:
                ui.button("もっと見る", on_click=more).props("outline color=primary")

    def search(value: str | None = None) -> None:
        state.set_query((search_input.value if value is None else value) or "")
        update_viewer()
        render_gallery()

    def more() -> None:
        state.load_more()
        render_gallery()

    def change_size(size: str) -> None:
        state.set_card_size(size)
        render_gallery()

    ui.colors(primary=TOKENS["accent"])
    with ui.column().classes("pv-shell"):
        with ui.element("div").classes("pv-header"):
            with ui.column().classes("gap-0"):
                ui.label("PixivVault").classes("pv-title")
                ui.label("NiceGUI 移植デモ / 架空の作品120件").classes("pv-subtitle")
            ui.badge("Phase 1 · デモ").props("outline color=primary")

        with ui.element("div").classes("pv-nav"):
            for name in (
                "ダウンロード", "キュー", "失敗リスト", "重複整理",
                "学習", "ランキング", "ライブラリ", "おすすめ",
            ):
                if name == "ランキング":
                    ui.button(name).classes("pv-tab pv-tab-active").props("unelevated")
                else:
                    ui.button(f"{name} · 未移植").classes("pv-tab pv-tab-disabled").props(
                        "flat disable"
                    )

        with ui.element("div").classes("pv-toolbar"):
            search_input = ui.input(
                placeholder="作品名・作者で検索",
                on_change=lambda event: search(event.value),
            ).props(
                "outlined dense clearable dark debounce=200"
            ).classes("pv-search")
            search_input.on("keydown.enter", lambda _: search())
            ui.button("検索", on_click=search).props("color=primary")
            ui.label("カードサイズ").classes("pv-subtitle")
            for title, size in (("小", "small"), ("中", "medium"), ("大", "large")):
                ui.button(title, on_click=lambda _, s=size: change_size(s)).props(
                    "flat dense"
                )

        gallery = ui.column().classes("w-full gap-3")
        with ui.dialog() as viewer:
            with ui.column().classes("pv-viewer"):
                with ui.element("div").classes("pv-art pv-view-art") as viewer_art:
                    ui.label("デモ画像")
                viewer_title = ui.label("").classes("pv-view-title")
                viewer_author = ui.label("").classes("pv-subtitle")
                viewer_description = ui.label("").classes("pv-description")
                viewer_position = ui.label("").classes("pv-counter")
                with ui.row().classes("w-full justify-between gap-2"):
                    previous_button = ui.button("前へ", on_click=lambda: shift_viewer(-1)).props("flat")
                    ui.button("閉じる", on_click=close_viewer).props("flat")
                    next_button = ui.button("次へ", on_click=lambda: shift_viewer(1)).props("flat")
        viewer.on("hide", lambda _: state.close_viewer())
        viewer_elements.update(
            art=viewer_art, title=viewer_title, author=viewer_author,
            description=viewer_description, position=viewer_position,
            previous=previous_button, next=next_button,
        )
        render_gallery()
