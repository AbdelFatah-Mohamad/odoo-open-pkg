# -*- coding: utf-8 -*-
"""Shared fixtures: fake SDK objects so no test ever touches the network.

Patch points:
  * ``odoo.addons.ac_ads_connector.tools.meta_api.AdAccount`` — Meta AdObjects
  * ``GoogleAdsApi._build_sdk`` — Google client (grpc stack never constructed)
Real SDK exception classes are instantiated (Meta) or built via ``__new__``
(Google — its constructor needs protos) so isinstance checks in the mapping
code exercise the production path.
"""
from unittest.mock import MagicMock

from facebook_business.exceptions import FacebookRequestError

from odoo.tests import TransactionCase, tagged  # noqa: F401 - re-exported


def make_fb_error(code=1, subcode=None, status=400, message="boom",
                  transient=False, headers=None):
    body = {"error": {
        "message": message, "type": "OAuthException" if code in (190, 102) else "FacebookApiException",
        "code": code, "error_subcode": subcode, "is_transient": transient,
    }}
    return FacebookRequestError(message, {}, status, headers or {}, body)


def make_google_error(kind=None, message="boom", status_name="INVALID_ARGUMENT",
                      retry_seconds=None):
    """Build a GoogleAdsException lookalike via __new__ (its __init__ needs protos)."""
    from google.ads.googleads.errors import GoogleAdsException

    exc = GoogleAdsException.__new__(GoogleAdsException)
    error_code = MagicMock()
    error_code.WhichOneof.return_value = kind
    error = MagicMock()
    error.message = message
    error.error_code = error_code
    if retry_seconds is not None:
        error.details.quota_error_details.retry_delay.seconds = retry_seconds
    else:
        error.details = None
    failure = MagicMock()
    failure.errors = [error] if kind else []
    exc.failure = failure
    grpc_status = MagicMock()
    grpc_status.name = status_name
    call = MagicMock()
    call.code.return_value = grpc_status
    exc.error = call
    # Exception base state (never touched by __init__)
    exc.args = (message,)
    return exc


class FakeMetaNode:
    def __init__(self, data):
        self._data = dict(data)

    def export_all_data(self):
        return dict(self._data)


class FakeGoogleCustomerRow:
    def __init__(self, cid, name="Acme", currency="USD", tz="Asia/Hebron"):
        customer = MagicMock()
        customer.id = int(cid)
        customer.descriptive_name = name
        customer.currency_code = currency
        customer.time_zone = tz
        self.customer = customer


class FakeGoogleSearchResponse:
    def __init__(self, rows, next_page_token=""):
        self._rows = rows
        self.next_page_token = next_page_token

    def __iter__(self):
        return iter(self._rows)


class AdsCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.meta_account = cls.env["ads.account"].create({
            "name": "Meta Test",
            "provider": "meta",
            "external_account_id": "act_123456789",
            "meta_access_token": "meta-token-AAAAAAAAAAAAAAAAAAAAAAAA",
            "meta_app_secret": "s3cr3t",
            "meta_page_id": "424242",
        })
        cls.google_account = cls.env["ads.account"].create({
            "name": "Google Test",
            "provider": "google",
            "external_account_id": "123-456-7890",
            "google_auth_mode": "oauth_refresh",
            "google_developer_token": "dev-token-XXXXXXXXXXXXXXXXXXXXXXXX",
            "google_client_id": "cid.apps.googleusercontent.com",
            "google_client_secret": "gsecret",
            "google_refresh_token": "1//refresh-XXXXXXXXXXXXXXXXXXXXXXXX",
            "google_login_customer_id": "999-888-7777",
        })
