# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.ac_ads_connector.tools.ads_api import AdsApiError
from odoo.addons.ac_ads_connector.tools.meta_api import MetaAdsApi

from .common import AdsCommon, FakeMetaNode, make_fb_error

AD_ACCOUNT = "odoo.addons.ac_ads_connector.tools.meta_api.AdAccount"


@tagged("post_install", "-at_install")
class TestMetaApi(AdsCommon):

    def _api(self):
        return self.meta_account._get_api()

    # ----------------------------------------------------------- credentials
    def test_missing_token_is_account_error(self):
        self.meta_account.meta_access_token = False
        with self.assertRaises(AdsApiError) as ctx:
            self._api()._check_credentials()
        self.assertEqual(ctx.exception.failure_type, "account")

    def test_external_id_sanitized(self):
        self.assertEqual(self.meta_account.external_account_id, "123456789")

    def test_per_account_session_isolation(self):
        other = self.meta_account.copy({"name": "Meta 2", "external_account_id": "987"})
        other.meta_access_token = "other-token-BBBBBBBBBBBBBBBBBBBBBBBB"
        token_a = self._api().sdk._session.access_token
        token_b = other._get_api().sdk._session.access_token
        self.assertNotEqual(token_a, token_b)
        self.assertEqual(token_a, "meta-token-AAAAAAAAAAAAAAAAAAAAAAAA")

    # ---------------------------------------------------------- error mapping
    def test_error_mapping(self):
        api = self._api()
        cases = [
            (make_fb_error(code=190, status=401), "auth"),
            (make_fb_error(code=10, status=403), "permission"),
            (make_fb_error(code=200, status=403), "permission"),
            (make_fb_error(code=4, status=403), "rate_limit"),
            (make_fb_error(code=613, status=403), "rate_limit"),
            (make_fb_error(code=100, subcode=1487534), "too_much_data"),
            (make_fb_error(code=1, status=500, transient=True), "recoverable"),
            (make_fb_error(code=100, status=400), "unrecoverable"),
        ]
        for exc, expected in cases:
            mapped = api._map_exception(exc)
            self.assertEqual(mapped.failure_type, expected,
                             f"code {exc.api_error_code()} -> {mapped.failure_type}")

    def test_usage_header_cooldown(self):
        api = self._api()
        headers = {"x-business-use-case-usage":
                   '{"123": [{"type": "ads_insights", "call_count": 10, "total_cputime": 5, '
                   '"total_time": 4, "estimated_time_to_regain_access": 7}]}'}
        self.assertEqual(api._usage_cooldown(headers), 7 * 60)
        headers = {"x-app-usage": '{"call_count": 99, "total_cputime": 10, "total_time": 10}'}
        self.assertEqual(api._usage_cooldown(headers), 300)
        self.assertFalse(api._usage_cooldown(
            {"x-app-usage": '{"call_count": 10, "total_cputime": 10, "total_time": 10}'}))

    def test_cooldown_blocks_next_call_without_sdk_traffic(self):
        api = self._api()
        fn = MagicMock(side_effect=make_fb_error(code=4, status=403))
        with self.assertRaises(AdsApiError):
            api._execute(fn, op="probe", retries=0)
        # code 4 has no retry_after header here -> no auto cooldown; set one
        self.meta_account._set_cooldown(600, "test")
        fn.reset_mock()
        with self.assertRaises(AdsApiError) as ctx:
            api._execute(fn, op="probe")
        self.assertEqual(ctx.exception.failure_type, "rate_limit")
        self.assertFalse(fn.called, "no SDK call may happen during cooldown")
        self.assertGreater(ctx.exception.retry_after, 0)

    def test_success_usage_capture_sets_cooldown(self):
        api = self._api()
        result = MagicMock()
        result.headers = {"x-app-usage": '{"call_count": 97, "total_cputime": 1, "total_time": 1}'}
        del result.http_headers  # force the plain-attr accessor path
        value = api._execute(lambda: result, op="probe")
        self.assertIs(value, result)
        self.assertTrue(self.meta_account.cooldown_until)
        self.assertIn("call_count", str(self.meta_account.rate_limit_state))

    # ------------------------------------------------------- test connection
    def test_action_test_connection_success(self):
        node = FakeMetaNode({"name": "ACME Ads", "account_status": 1,
                             "currency": "USD", "timezone_name": "Asia/Hebron"})
        with patch(AD_ACCOUNT) as AdAccountCls:
            AdAccountCls.return_value.api_get.return_value = node
            self.meta_account.action_test_connection()
        self.assertEqual(self.meta_account.state, "connected")
        self.assertEqual(self.meta_account.platform_account_name, "ACME Ads")
        self.assertEqual(self.meta_account.currency_id.name, "USD")
        self.assertEqual(self.meta_account.tz_name, "Asia/Hebron")

    def test_action_test_connection_failure_sets_error(self):
        with patch(AD_ACCOUNT) as AdAccountCls:
            AdAccountCls.return_value.api_get.side_effect = make_fb_error(code=190, status=401)
            result = self.meta_account.action_test_connection()
        self.assertEqual(result["params"]["type"], "danger")
        self.assertIn("Connection failed", result["params"]["message"])
        self.assertEqual(self.meta_account.state, "error")

    def test_token_health_flags_auth_failure(self):
        self.meta_account.state = "connected"
        with patch(AD_ACCOUNT) as AdAccountCls:
            AdAccountCls.return_value.api_get.side_effect = make_fb_error(code=190, status=401)
            self.env["ads.account"]._cron_token_health()
        self.assertEqual(self.meta_account.state, "error")

    # ------------------------------------------------------------- utilities
    def test_redaction_hides_tokens(self):
        api = self._api()
        text = api._redact(("meta-token-AAAAAAAAAAAAAAAAAAAAAAAA",), {})
        self.assertNotIn("AAAAAAAA", text)
        self.assertIn("***", text)

    def test_hash_pii_vectors(self):
        # sha256("test@example.com")
        self.assertEqual(
            MetaAdsApi.hash_pii("  Test@Example.com "),
            "973dfe463ec85785f5f95af5ba3906eedb2d931c24e69824a89ea65dba4e813b")
        self.assertEqual(MetaAdsApi.hash_pii("+970 (59) 123-4567", kind="phone"),
                         MetaAdsApi.hash_pii("970591234567", kind="phone"))
        self.assertIsNone(MetaAdsApi.hash_pii(""))

    def test_money_normalizers(self):
        self.assertEqual(MetaAdsApi._from_minor("1250"), 12.5)
        self.assertEqual(MetaAdsApi._to_minor(12.5), 1250)
        self.assertEqual(MetaAdsApi._from_micros(2_500_000), 2.5)
        self.assertEqual(MetaAdsApi._to_micros(2.5), 2_500_000)

    def test_parse_dt(self):
        dt = MetaAdsApi._parse_dt("2026-08-01T12:30:00+0300".replace("+0300", "+03:00"))
        self.assertEqual((dt.hour, dt.tzinfo), (9, None))
        self.assertIsNone(MetaAdsApi._parse_dt(""))
