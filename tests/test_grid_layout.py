from pixivvault_nicegui.ui.grid_layout import (
    CARD_GAP, CARD_MIN_WIDTH, DEFAULT_CARD_SIZE, MAX_COLUMNS,
    card_width, columns_for_width,
)


def test_original_modes_and_narrow_window_fallback():
    assert (CARD_MIN_WIDTH, CARD_GAP, DEFAULT_CARD_SIZE) == (170, 12, "large")
    assert MAX_COLUMNS == {"large": 6, "medium": 8, "small": 12}
    assert [columns_for_width(1920, mode) for mode in MAX_COLUMNS] == [6, 8, 10]
    assert [columns_for_width(1280, mode) for mode in MAX_COLUMNS] == [6, 7, 7]
    assert columns_for_width(170) == 1
    assert columns_for_width(351) == 1
    assert columns_for_width(352) == 2
    assert columns_for_width(0) == 1
    assert columns_for_width(-1) == 1
    assert columns_for_width(1920, "unknown") == 6


def test_card_width_never_drops_below_minimum():
    for width in (0, 140, 170, 352, 700, 1280, 1920):
        for mode in MAX_COLUMNS:
            cols = columns_for_width(width, mode)
            extent = card_width(width, mode)
            assert extent >= CARD_MIN_WIDTH
            if width >= CARD_MIN_WIDTH:
                assert cols * extent + (cols - 1) * CARD_GAP <= width + 1e-8
