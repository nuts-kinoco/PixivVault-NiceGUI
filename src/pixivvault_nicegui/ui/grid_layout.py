"""Original PixivVault card column limits, independent of the UI framework."""

CARD_MIN_WIDTH = 170
CARD_GAP = 12
DEFAULT_CARD_SIZE = "large"
MAX_COLUMNS = {"large": 6, "medium": 8, "small": 12}


def columns_for_width(available_width: float, size: str = DEFAULT_CARD_SIZE) -> int:
    """Fit 170px cards and 12px gaps without exceeding the mode's column cap."""
    if available_width <= 0:
        return 1
    maximum = MAX_COLUMNS.get(size, MAX_COLUMNS[DEFAULT_CARD_SIZE])
    return max(1, min(maximum, int((available_width + CARD_GAP) // (CARD_MIN_WIDTH + CARD_GAP))))


def card_width(available_width: float, size: str = DEFAULT_CARD_SIZE) -> float:
    """Return the original max_extent equivalent for the selected width."""
    columns = columns_for_width(available_width, size)
    return max(CARD_MIN_WIDTH, (available_width - CARD_GAP * (columns - 1)) / columns)


__all__ = ["CARD_MIN_WIDTH", "CARD_GAP", "DEFAULT_CARD_SIZE", "MAX_COLUMNS", "columns_for_width", "card_width"]
