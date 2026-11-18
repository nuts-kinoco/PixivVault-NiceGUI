"""Measured trend-panel capacity and display-only score normalization."""

import math


def layout_mode(width, height, aspect, current='rail'):
    width = max(0.0, float(width) - 48.0)
    height = max(0.0, float(height) - 48.0 - 30.0)
    aspect = float(aspect) if aspect and aspect > 0 else 1.0
    rail_width = width - 440.0 - 24.0
    if rail_width < 320.0:
        return 'fold'

    def area(w, h):
        w, h = max(0.0, w), max(0.0, h)
        fit_w = min(w, h * aspect)
        return fit_w * min(h, fit_w / aspect)

    rail = area(rail_width, height)
    band = area(width, height - 192.0 - 24.0)
    if current == 'band':
        return 'band' if band >= rail * .95 else 'rail'
    return 'rail' if rail >= band * .95 else 'band'


class PreferenceCapacity:
    def __init__(self):
        self.rows = None

    def update(self, height):
        height = max(0.0, float(height))
        if self.rows is None:
            self.rows = max(1, math.floor((height + 8.5) / 25.0))
        else:
            if height >= (self.rows + 1) * 25 - 8 + 0.5:
                self.rows = max(self.rows, math.floor((height + 7.5) / 25.0))
            elif self.rows > 1 and height < self.rows * 25 - 8 - 0.5:
                self.rows = max(1, min(self.rows, math.floor((height + 8.5) / 25.0)))
        return self.rows


def displayed_rows(rows, mode, rail_rows):
    limit = 6 if mode == 'band' else 5 if mode == 'fold' else rail_rows
    shown = tuple(rows[:limit])
    peak = max((row['score'] for row in shown), default=0)
    return tuple((row, min(1.0, max(0.0, row['score']) / peak) if peak > 1e-6 else 0.0)
                 for row in shown)
