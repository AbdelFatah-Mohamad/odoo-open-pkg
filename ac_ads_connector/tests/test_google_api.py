# -*- coding: utf-8 -*-
import json
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.ac_ads_connector.tools.ads_api import AdsApiError
from odoo.addons.ac_ads_connector.tools.google_api import GoogleAdsApi

from .common import AdsCommon, FakeGoogleCustomerRow, FakeGoogleSearchResponse, make_google_error

BUILD_SDK = "odoo.addons.ac_ads_connector.tools.google_api.GoogleAdsApi._build_sdk"


def _fake_client(rows_pages):
    """A GoogleAdsClient stand-in exposing GoogleAdsService.search over fixed pages."""
    client = MagicMock()
    service = MagicMock()
    pages = list(rows_pages)

    def search(request=None):
        token = getattr(request, "page_token", "") or ""
        index = int(token or 0)
        next_token = str(index + 1) if index + 1 < len(pages) else ""
        return FakeGoogleSearchResponse(pages[index], next_page_token=next_token)

    service.search.side_effect = search
    client.get_service.return_value = service
    request_factory = MagicMock(side_effect=lambda **kw: MagicMock(**kw, page_token=""))
    client.get_type.return_value = request_factory
    return client


@tagged("post_install", "-at_install")
class TestGoogleApi(AdsCommon):

    def _api(self):
        return self.google_account._get_api()

    # ----------------------------------------------------------- credentials
    def test_missing_developer_token(self):
        self.google_account.google_developer_token = False
        with self.assertRaises(AdsApiError) as ctx:
            self._api()._check_credentials()
        self.assertEqual(ctx.exception.failure_type, "account")

    def test_oauth_mode_requires_refresh_token(self):
        self.google_account.google_refresh_token = False
        with self.assertRaises(AdsApiError):
            self._api()._check_credentials()

    def test_sa_mode_requires_key_and_rejects_bad_json(self):
        self.google_account.google_auth_mode = "service_account"
        with self.assertRaises(AdsApiError):
            self._api()._check_credentials()
        self.google_account.google_sa_json = "{not json"
        with self.assertRaises(AdsApiError) as ctx:
            self._api()._build_sdk()
        self.assertEqual(ctx.exception.failure_type, "account")

    def test_customer_id_digits(self):
        self.assertEqual(self._api().customer_id, "1234567890")

    # ------------------------------------------------------------ sdk config
    def test_load_from_dict_payload(self):
        with patch("odoo.addons.ac_ads_connector.tools.google_api._lazy_imports") as lazy:
            client_cls = MagicMock()
            lazy.return_value = (client_cls, Exception, Exception, MagicMock())
            self._api()._build_sdk()
        (config,), kwargs = client_cls.load_from_dict.call_args
        self.assertEqual(config["developer_token"], "dev-token-XXXXXXXXXXXXXXXXXXXXXXXX")
        self.assertEqual(config["refresh_token"], "1//refresh-XXXXXXXXXXXXXXXXXXXXXXXX")
        self.assertEqual(config["login_customer_id"], "9998887777")
        self.assertTrue(config["use_proto_plus"])
        self.assertEqual(kwargs["version"], "v25")

    def test_service_account_no_impersonation(self):
        self.google_account.google_auth_mode = "service_account"
        self.google_account.google_sa_json = json.dumps({
            "type": "service_account", "client_email": "sa@x.iam.gserviceaccount.com"})
        with patch("odoo.addons.ac_ads_connector.tools.google_api._lazy_imports") as lazy:
            client_cls, sa_module = MagicMock(), MagicMock()
            lazy.return_value = (client_cls, Exception, Exception, sa_module)
            self._api()._build_sdk()
        _args, sa_kwargs = sa_module.Credentials.from_service_account_info.call_args
        self.assertEqual(sa_kwargs["scopes"], ["https://www.googleapis.com/auth/adwords"])
        _args, kwargs = client_cls.call_args
        self.assertEqual(kwargs["developer_token"], "dev-token-XXXXXXXXXXXXXXXXXXXXXXXX")
        self.assertEqual(kwargs["login_customer_id"], "9998887777")
        self.assertNotIn("subject", kwargs)

    # ---------------------------------------------------------- error mapping
    def test_error_mapping(self):
        api = self._api()
        quota = api._map_exception(make_google_error("quota_error", retry_seconds=3600))
        self.assertEqual(quota.failure_type, "rate_limit")
        self.assertEqual(quota.retry_after, 3600)
        auth = api._map_exception(make_google_error("authentication_error"))
        self.assertEqual(auth.failure_type, "auth")
        perm = api._map_exception(make_google_error("authorization_error"))
        self.assertEqual(perm.failure_type, "permission")
        val = api._map_exception(make_google_error("field_error"))
        self.assertEqual(val.failure_type, "validation")
        rec = api._map_exception(make_google_error(None, status_name="UNAVAILABLE"))
        self.assertEqual(rec.failure_type, "recoverable")
        unrec = api._map_exception(make_google_error(None, status_name="INVALID_ARGUMENT"))
        self.assertEqual(unrec.failure_type, "unrecoverable")

    def test_gaql_quote_blocks_injection(self):
        self.assertEqual(GoogleAdsApi._gaql_quote("2026-08-01"), "'2026-08-01'")
        with self.assertRaises(AdsApiError):
            GoogleAdsApi._gaql_quote("x' OR '1'='1")

    # ----------------------------------------------------------------- reads
    def test_get_account_info(self):
        client = _fake_client([[FakeGoogleCustomerRow("1234567890", name="Acme")]])
        with patch(BUILD_SDK, return_value=client):
            info = self._api().get_account_info()
        self.assertEqual(info["name"], "Acme")
        self.assertEqual(info["currency"], "USD")

    def test_get_account_info_wrong_customer(self):
        client = _fake_client([[FakeGoogleCustomerRow("55555")]])
        with patch(BUILD_SDK, return_value=client):
            with self.assertRaises(AdsApiError) as ctx:
                self._api().get_account_info()
        self.assertEqual(ctx.exception.failure_type, "account")

    def test_search_follows_page_tokens(self):
        pages = [[FakeGoogleCustomerRow("1")], [FakeGoogleCustomerRow("2")],
                 [FakeGoogleCustomerRow("3")]]
        client = _fake_client(pages)
        with patch(BUILD_SDK, return_value=client):
            got = list(self._api()._search("SELECT customer.id FROM customer"))
        self.assertEqual(len(got), 3)
        client = _fake_client(pages)
        with patch(BUILD_SDK, return_value=client):
            got = list(self._api()._search("SELECT customer.id FROM customer", max_pages=2))
        self.assertEqual(len(got), 2)

    def test_action_test_connection_google(self):
        client = _fake_client([[FakeGoogleCustomerRow("1234567890", name="Acme",
                                                      currency="EUR", tz="Europe/Paris")]])
        with patch(BUILD_SDK, return_value=client):
            self.google_account.action_test_connection()
        self.assertEqual(self.google_account.state, "connected")
        self.assertEqual(self.google_account.currency_id.name, "EUR")
        self.assertEqual(self.google_account.tz_name, "Europe/Paris")
