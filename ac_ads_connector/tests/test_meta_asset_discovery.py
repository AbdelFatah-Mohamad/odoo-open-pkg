# -*- coding: utf-8 -*-
"""The 'Fetch from Meta' button.

Setup's most common failure is a human copying the wrong long number out of Business
Settings — a System User ID, a pixel/dataset ID and an Ad Account ID are visually
identical. This asks Meta instead. Page Access Tokens have no UI at all in Business
Settings; /me/accounts is the only source.
"""
from unittest.mock import MagicMock, patch

from odoo.addons.ac_ads_connector.tools.meta_api import MetaAdsApi

from .common import TransactionCase, make_fb_error, tagged


def _fake_api(ad_accounts, pages):
    """Stand in for FacebookAdsApi: .call(...).json() per edge, in call order."""
    api = MagicMock()
    api.call.side_effect = [
        MagicMock(json=MagicMock(return_value={"data": ad_accounts})),
        MagicMock(json=MagicMock(return_value={"data": pages})),
    ]
    return api


@tagged("post_install", "-at_install")
class TestMetaAssetDiscovery(TransactionCase):

    def setUp(self):
        super().setUp()
        self.account = self.env["ads.account"].create({
            "name": "test", "provider": "meta", "meta_access_token": "SYSTEM_USER_TOKEN",
        })

    def _run(self, ad_accounts, pages):
        with patch("odoo.addons.ac_ads_connector.tools.meta_api.FacebookAdsApi",
                   return_value=_fake_api(ad_accounts, pages)):
            return self.account.action_fetch_meta_assets()

    def test_single_asset_is_filled_in(self):
        self._run(
            [{"id": "act_1761256974910372", "name": "abdelfatah", "currency": "EGP",
              "timezone_name": "Africa/Cairo"}],
            [{"id": "1234567890", "name": "AlShayeb", "access_token": "PAGE_TOKEN"}])
        # "act_" is stripped: the field stores the bare number Meta's other edges expect.
        self.assertEqual(self.account.external_account_id, "1761256974910372")
        self.assertEqual(self.account.meta_page_id, "1234567890")
        self.assertEqual(self.account.sudo().meta_page_access_token, "PAGE_TOKEN")

    def test_never_overwrites_what_the_user_typed(self):
        self.account.write({"external_account_id": "999", "meta_page_id": "888"})
        self._run([{"id": "act_111", "name": "other"}],
                  [{"id": "222", "name": "other page", "access_token": "T"}])
        self.assertEqual(self.account.external_account_id, "999")
        self.assertEqual(self.account.meta_page_id, "888")

    def test_ambiguous_choice_is_listed_not_guessed(self):
        result = self._run(
            [{"id": "act_1", "name": "Account One"}, {"id": "act_2", "name": "Account Two"}],
            [])
        self.assertFalse(self.account.external_account_id, "two candidates: must not guess")
        message = result["params"]["message"]
        self.assertIn("Account One", message)
        self.assertIn("Account Two", message)

    def test_no_ad_account_explains_the_actual_fix(self):
        result = self._run([], [{"id": "1", "name": "P", "access_token": "T"}])
        self.assertIn("Manage campaigns", result["params"]["message"])

    def test_missing_token_is_refused_before_any_call(self):
        self.account.sudo().meta_access_token = False
        result = self.account.action_fetch_meta_assets()
        self.assertEqual(result["params"]["type"], "danger")
        self.assertIn("System User Token", result["params"]["message"])

    def test_meta_error_surfaces_and_parks_the_account(self):
        with patch.object(MetaAdsApi, "list_assets",
                          side_effect=lambda *a, **k: (_ for _ in ()).throw(
                              __import__("odoo").addons.ac_ads_connector.tools.ads_api
                              .AdsApiError("Invalid OAuth access token", "auth", code=190))):
            result = self.account.action_fetch_meta_assets()
        self.assertEqual(result["params"]["type"], "danger")
        self.assertEqual(self.account.state, "error")

    def test_discovery_works_before_an_ad_account_is_known(self):
        """The whole point: _check_credentials demands the very id we are discovering."""
        self.assertFalse(self.account.external_account_id)
        self._run([{"id": "act_5", "name": "Only"}], [])
        self.assertEqual(self.account.external_account_id, "5")
