import pytest

from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.models import Artwork


def test_demo_artworks_are_fictional_and_stable():
    first = generate_demo_artworks(55)
    assert first == generate_demo_artworks(55)
    assert len(first) == 55
    assert all(isinstance(item, Artwork) for item in first)
    assert len({item.work_id for item in first}) == 55
    assert first[0].work_id == "demo-0001"
    assert first[49].work_id == "demo-0050"
    assert all(item.title and item.author and item.description for item in first)
    assert len({item.aspect_ratio for item in first}) > 1


def test_demo_empty_and_negative_count():
    assert generate_demo_artworks(0) == ()
    with pytest.raises(ValueError):
        generate_demo_artworks(-1)
