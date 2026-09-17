"""Tests for src/services/integrations/gdrive/errors.py — the fixed HTTP contract (T084)."""

import pytest
from fastapi import HTTPException

from src.services.integrations.gdrive.errors import (
    GDriveConfigError,
    GDriveError,
    GDriveNeedsAuthorizationError,
    GDriveNotEnabledError,
    GDriveNotFoundError,
    GDrivePermissionError,
    GDriveQuotaExceededError,
    GDriveTransientError,
    GDriveUnauthorizedError,
    to_http_exception,
)

_CONTRACT = [
    (GDriveNotEnabledError, 409, "Video : Google Drive storage is not enabled on this instance"),
    (GDriveNeedsAuthorizationError, 409, "Video : Google Drive needs re-authorization by the operator"),
    (GDriveConfigError, 409, "Video : Google Drive is not configured correctly"),
    (GDriveQuotaExceededError, 507, "Video : Google Drive is out of storage space"),
    (GDrivePermissionError, 502, "Video : Could not set sharing on the Google Drive file"),
    (GDriveTransientError, 502, "Video : Google Drive is unreachable, please try again"),
    # Internal subclasses keep their parent's HTTP contract.
    (GDriveUnauthorizedError, 502, "Video : Google Drive is unreachable, please try again"),
    (GDriveNotFoundError, 502, "Video : Could not set sharing on the Google Drive file"),
]


@pytest.mark.parametrize("cls,status,detail", _CONTRACT)
def test_http_mapping_is_fixed_per_class(cls, status, detail):
    exc = to_http_exception(cls())
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status
    assert exc.detail == detail
    assert issubclass(cls, GDriveError)


@pytest.mark.parametrize("cls,_status,detail", _CONTRACT)
def test_runtime_message_never_reaches_the_http_detail(cls, _status, detail):
    err = cls("refresh_token=1//SECRET access=ya29.SECRET path=/srv/token.json", reason="whatever")
    assert err.reason == "whatever"
    assert "SECRET" in str(err)  # message is for logs / CLI
    http = to_http_exception(err)
    assert http.detail == detail
    assert "SECRET" not in http.detail and "/srv/token.json" not in http.detail


def test_default_message_is_the_detail():
    assert str(GDriveQuotaExceededError()) == GDriveQuotaExceededError.detail
    assert GDriveError().reason is None
