"""Offline only: no cookie, HTTP client, or actual Pixiv account mutations."""

import pytest

from pixivvault_nicegui.bookmark_write_evidence import (
    AddEvidence,
    authorize_compensating_delete,
    inspect_add_response,
    inspect_final_state,
    positive_id,
    successful_api_response,
)


@pytest.mark.parametrize("value", (None, True, False, 0, -1, "", "abc", "0",
                                   " 12", "12\n", "1/2", "1" * 21, 1.2, []))
def test_unacceptable_identifiers_are_rejected(value):
    assert positive_id(value) is None


def test_valid_identifiers_are_normalized_without_network():
    assert positive_id(123) == "123"
    assert positive_id("00123") == "00123"


@pytest.mark.parametrize(("status", "payload"), (
    (201, {"error": False}),
    (400, {"error": False}),
    (200, {"error": True}),
    (200, {"error": None}),
    (200, {"body": {"id": "7"}}),
    (True, {"error": False}),
    ("200", {"error": False}),
    (200, []),
))
def test_http_or_api_error_never_authorizes_delete(status, payload):
    assert not successful_api_response(status, payload)
    evidence = inspect_add_response(status, {**payload, "body": {"last_bookmark_id": "9"}}
                                    if isinstance(payload, dict) else payload)
    assert not evidence.accepted
    assert evidence.created_bookmark_id is None


def test_single_created_id_from_add_response_can_be_correlated():
    evidence = inspect_add_response(200, {
        "error": False, "body": {"last_bookmark_id": "987"}
    })
    assert evidence == AddEvidence(True, "987", "accepted_with_id")
    assert authorize_compensating_delete(
        initial_bookmark_id=None, observed_bookmark_id="987", add=evidence)
    assert not authorize_compensating_delete(
        initial_bookmark_id=None, observed_bookmark_id="988", add=evidence)
    assert not authorize_compensating_delete(
        initial_bookmark_id="900", observed_bookmark_id="987", add=evidence)


@pytest.mark.parametrize("body", (
    {},
    {"id": False},
    {"last_bookmark_id": "3", "bookmarkId": "4"},
    {"last_bookmark_id": "7/8"},
))
def test_ambiguous_or_invalid_add_receipt_blocks_compensation(body):
    evidence = inspect_add_response(200, {"error": False, "body": body})
    assert evidence.accepted
    assert not evidence.authorizes_correlated_delete
    assert not authorize_compensating_delete(
        initial_bookmark_id=None, observed_bookmark_id="3", add=evidence)


def test_failed_add_cannot_delete_even_when_response_contains_id():
    evidence = inspect_add_response(400, {
        "error": False, "body": {"last_bookmark_id": "456"}
    })
    assert not evidence.authorizes_correlated_delete
    assert not authorize_compensating_delete(
        initial_bookmark_id=None, observed_bookmark_id="456", add=evidence)


def test_successful_delete_and_confirmed_absence_prove_roundtrip():
    result = inspect_final_state(
        delete_http_status=200, delete_response={"error": False},
        final_state_known=True, final_bookmark_id=None)
    assert result.complete_roundtrip
    assert result.status == "roundtrip_restored"
    assert not result.manual_attention_required


@pytest.mark.parametrize(("status", "payload"), (
    (500, {"error": False}),
    (200, {"error": True}),
    (200, {"error": None}),
    (201, {"error": False}),
))
def test_missing_successful_delete_response_cannot_be_reported_as_pass(status, payload):
    result = inspect_final_state(
        delete_http_status=status, delete_response=payload,
        final_state_known=True, final_bookmark_id=None)
    assert result.restored
    assert not result.complete_roundtrip
    assert result.status == "restored_but_delete_unconfirmed"


def test_present_bookmark_requires_manual_attention_even_after_200_response():
    result = inspect_final_state(
        delete_http_status=200, delete_response={"error": False},
        final_state_known=True, final_bookmark_id="987")
    assert not result.complete_roundtrip
    assert result.manual_attention_required
    assert result.status == "bookmark_still_present"


def test_unknown_final_state_is_never_reported_as_restored():
    result = inspect_final_state(
        delete_http_status=200, delete_response={"error": False},
        final_state_known=False, final_bookmark_id=None)
    assert not result.restored
    assert not result.complete_roundtrip
    assert result.manual_attention_required
