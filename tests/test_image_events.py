import pytest

from pixivvault_nicegui.image_events import source_matches


@pytest.mark.parametrize("actual", [
    "/media/demo-0001/0/view.png#request-4",
    "http://127.0.0.1:8089/media/demo-0001/0/view.png#request-4",
    "http://[::1]:8089/media/demo-0001/0/view.png#request-4",
])
def test_browser_absolute_source_matches_same_request(actual):
    assert source_matches(actual, "/media/demo-0001/0/view.png#request-4")


@pytest.mark.parametrize("actual", [
    None, {}, "", "data:image/png;base64,aaa",
    "/media/demo-0001/0/view.png#request-3",
    "/media/demo-0001/1/view.png#request-4",
    "/media/demo-0002/0/view.png#request-4",
    "/media/demo-0001/0/view.png?different=1#request-4",
])
def test_old_request_or_other_source_is_not_accepted(actual):
    assert not source_matches(actual, "/media/demo-0001/0/view.png#request-4")
