"""K13 synthetic browser receipt admission, without importing Playwright."""
from __future__ import annotations

import pytest

from tools.check_k13_first_page_retry_browser import scoped_success


def valid_receipt():
    return dict(checks=["daily", "failure", "retry"], page_errors=[], console_errors=[],
                external_requests=[], external_sockets_denied=0, normal_exit=True,
                forced_cleanup=False, port_released=True, shutdown_acknowledged=True,
                listener_owner_verified=True, owned_tree={"observed_count": 3,
                "remaining_count": 0, "errors": []}, source_changed=False,
                harness_changed=False)


def test_bounded_synthetic_receipt_is_eligible_for_limited_success():
    assert scoped_success(valid_receipt()) is True


@pytest.mark.parametrize("diagnostic", [
    "error_class", "shutdown_error_class", "normal_shutdown_timeout",
    "cleanup_error_class", "cleanup_owner_unverified", "cleanup_root_timeout",
    "cleanup_root_error_class",
])
def test_any_cleanup_or_timeout_diagnostic_rejects_false_green(diagnostic):
    receipt = valid_receipt()
    receipt[diagnostic] = True
    # Even an eventual exit-code-zero cannot cure an earlier cleanup failure.
    assert scoped_success(receipt) is False


@pytest.mark.parametrize("change", [
    {"forced_cleanup": True},
    {"normal_exit": False},
    {"port_released": False},
    {"external_sockets_denied": 1},
    {"shutdown_acknowledged": False},
    {"listener_owner_verified": False},
    {"source_changed": True},
    {"harness_changed": True},
    {"owned_tree": {"remaining_count": 1, "errors": []}},
    {"owned_tree": {"remaining_count": None, "errors": ["observer_not_finished"]}},
])
def test_unowned_or_incomplete_recovery_cannot_pass(change):
    receipt = valid_receipt()
    receipt.update(change)
    assert scoped_success(receipt) is False
