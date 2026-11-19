from PIL import Image
import pytest

from tools.p5_design_evidence import PROFILES, SCREENS, compose, selected_screens


def test_default_tour_excludes_download_and_keeps_explicit_replay():
    assert 'download' not in selected_screens(None, include_download=False)
    assert set(selected_screens(None, include_download=True)) == set(SCREENS)
    with pytest.raises(ValueError, match='include-download'):
        selected_screens(['download'], include_download=False)
    assert selected_screens(['download'], include_download=True) == ['download']
    assert PROFILES == ('normal', 'empty', 'long')


def test_comparison_keeps_native_pixel_sizes_and_order(tmp_path):
    left = tmp_path / 'left.png'
    right = tmp_path / 'right.png'
    comparison = tmp_path / 'comparison.png'
    Image.new('RGB', (1280, 800), '#111418').save(left)
    Image.new('RGB', (1280, 800), '#222222').save(right)
    compose(left, right, comparison, (1280, 800))
    with Image.open(comparison) as result:
        assert result.size == (2560, 800)
        assert result.getpixel((1279, 799)) == (17, 20, 24)
        assert result.getpixel((1280, 0)) == (34, 34, 34)
    with pytest.raises(ValueError, match='1280'):
        compose(left, right, comparison, (1920 - 1, 1080))
