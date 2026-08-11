# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.ac_ads_connector.models import ads_account as account_module
from odoo.addons.ac_ads_connector.tools.google_api import GoogleAdsApi
from odoo.addons.ac_ads_connector.tools.meta_api import MetaAdsApi

from .common import AdsCommon
from .test_push_queue import FakePushApi

AD_ACCOUNT = "odoo.addons.ac_ads_connector.tools.meta_api.AdAccount"


class FakeCreateApi(FakePushApi):
    create_calls = []

    def create_object(self, object_type, record, values):
        self._maybe_raise()
        type(self).create_calls.append((object_type, dict(values)))
        result = {"external_id": f"new-{object_type}",
                  "resource_name": f"customers/1/{object_type}s/900",
                  "budget_resource": "customers/1/campaignBudgets/901"}
        if values.get("creative"):
            result["creative_external_id"] = "cr-new"
        return result


@tagged("post_install", "-at_install")
class TestPushbackCreate(AdsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.meta_account.state = "connected"
        cls.google_account.state = "connected"

    def setUp(self):
        super().setUp()
        FakeCreateApi.calls = []
        FakeCreateApi.create_calls = []
        FakeCreateApi.raise_error = None
        patcher = patch.dict(account_module.PROVIDER_API,
                             {"meta": FakeCreateApi, "google": FakeCreateApi})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _draft_campaign(self, account, **kw):
        vals = {"title": "Draft Camp", "account_id": account.id,
                "objective": "leads", "budget_type": "daily",
                "budget_amount": 25.0}
        vals.update(kw)
        return self.env["ads.campaign"].create(vals)

    # ---------------------------------------------------------------- drafts
    def test_new_campaign_starts_draft(self):
        campaign = self._draft_campaign(self.meta_account)
        self.assertEqual(campaign.sync_status, "draft")
        self.assertFalse(campaign.external_id)
        self.assertTrue(campaign.utm_campaign_id)

    def test_meta_campaign_create_flow(self):
        campaign = self._draft_campaign(self.meta_account)
        campaign.action_push()
        self.assertEqual(campaign.external_id, "new-campaign")
        self.assertEqual(campaign.sync_status, "synced")
        object_type, values = FakeCreateApi.create_calls[0]
        self.assertEqual(object_type, "campaign")
        self.assertEqual(values["objective_raw"], "OUTCOME_LEADS")
        self.assertEqual(values["special_ad_categories"], [])
        job = self.env["ads.push.job"].search([("res_id", "=", campaign.id),
                                               ("res_model", "=", "ads.campaign")])
        self.assertEqual(job.state, "done")
        self.assertEqual(job.result_external_id, "new-campaign")

    def test_google_campaign_create_writes_resources(self):
        campaign = self._draft_campaign(self.google_account, objective="traffic")
        campaign.action_push()
        self.assertEqual(campaign.external_id, "new-campaign")
        self.assertEqual(campaign.google_budget_resource,
                         "customers/1/campaignBudgets/901")
        self.assertEqual((campaign.provider_data or {}).get("resource_name"),
                         "customers/1/campaigns/900")

    def test_google_campaign_requires_budget(self):
        campaign = self._draft_campaign(self.google_account, budget_amount=0.0)
        with self.assertRaises(UserError):
            campaign.action_push()

    def test_adset_requires_pushed_campaign(self):
        campaign = self._draft_campaign(self.meta_account)
        adset = self.env["ads.adset"].create({
            "name": "Draft Set", "campaign_id": campaign.id,
            "sync_status": "draft",
            "optimization_goal": "LEAD_GENERATION",
            "billing_event": "IMPRESSIONS",
            "targeting": {"geo_locations": {"countries": ["PS"]}}})
        with self.assertRaises(UserError):
            adset.action_push()
        campaign.action_push()
        adset.action_push()
        self.assertEqual(adset.external_id, "new-adset")
        _object_type, values = FakeCreateApi.create_calls[-1]
        self.assertEqual(values["campaign_external_id"], "new-campaign")
        self.assertEqual(values["targeting"], {"geo_locations": {"countries": ["PS"]}})

    def test_meta_adset_validation(self):
        campaign = self._draft_campaign(self.meta_account)
        campaign.action_push()
        adset = self.env["ads.adset"].create({
            "name": "No targeting", "campaign_id": campaign.id,
            "sync_status": "draft"})
        with self.assertRaises(UserError) as ctx:
            adset.action_push()
        self.assertIn("targeting", str(ctx.exception))

    def test_google_ad_creation_refused(self):
        campaign = self._draft_campaign(self.google_account, objective="traffic")
        campaign.action_push()
        adset = self.env["ads.adset"].create({
            "name": "AG", "campaign_id": campaign.id, "sync_status": "draft"})
        adset.action_push()
        ad = self.env["ads.ad"].create({
            "name": "G Ad", "adset_id": adset.id, "sync_status": "draft"})
        with self.assertRaises(UserError) as ctx:
            ad.action_push()
        self.assertIn("Google Ads itself", str(ctx.exception))

    def test_meta_ad_needs_creative_then_creates(self):
        campaign = self._draft_campaign(self.meta_account)
        campaign.action_push()
        adset = self.env["ads.adset"].create({
            "name": "Set", "campaign_id": campaign.id, "sync_status": "draft",
            "optimization_goal": "LEAD_GENERATION", "billing_event": "IMPRESSIONS",
            "targeting": {"geo_locations": {"countries": ["PS"]}}})
        adset.action_push()
        ad = self.env["ads.ad"].create({
            "name": "New Ad", "adset_id": adset.id, "sync_status": "draft"})
        with self.assertRaises(UserError):
            ad.action_push()
        creative = self.env["ads.creative"].create({
            "name": "OSS", "account_id": self.meta_account.id,
            "object_story_spec": {"page_id": "424242",
                                  "link_data": {"link": "https://alshayeb.ps"}}})
        ad.creative_id = creative
        ad.action_push()
        self.assertEqual(ad.external_id, "new-ad")
        self.assertEqual(creative.external_id, "cr-new")

    # -------------------------------------------------- provider client layer
    def test_meta_client_create_campaign_params(self):
        api = MetaAdsApi(self.meta_account)
        with patch(AD_ACCOUNT) as AdAccountCls:
            AdAccountCls.return_value.create_campaign.return_value = {"id": "777"}
            result = api.create_object("campaign", None, {
                "title": "Client Camp", "objective_raw": "OUTCOME_LEADS",
                "budget_type": "daily", "budget_amount": 12.5,
                "special_ad_categories": []})
        params = AdAccountCls.return_value.create_campaign.call_args.kwargs["params"]
        self.assertEqual(params["status"], "PAUSED")
        self.assertEqual(params["objective"], "OUTCOME_LEADS")
        self.assertEqual(params["daily_budget"], 1250)
        self.assertEqual(params["special_ad_categories"], [])
        self.assertEqual(result["external_id"], "777")

    def test_google_client_create_campaign_atomic(self):
        api = GoogleAdsApi(self.google_account)
        client = MagicMock()
        response = SimpleNamespace(mutate_operation_responses=[
            SimpleNamespace(campaign_budget_result=SimpleNamespace(
                resource_name="customers/1/campaignBudgets/55")),
            SimpleNamespace(campaign_result=SimpleNamespace(
                resource_name="customers/1/campaigns/66")),
        ])
        client.get_service.return_value.mutate.return_value = response
        with patch.object(GoogleAdsApi, "_build_sdk", return_value=client):
            result = api.create_object("campaign", None, {
                "title": "G Camp", "objective": "traffic",
                "budget_type": "daily", "budget_amount": 30.0})
        kwargs = client.get_service.return_value.mutate.call_args.kwargs
        self.assertEqual(len(kwargs["mutate_operations"]), 2)
        self.assertEqual(kwargs["customer_id"], "1234567890")
        operation = kwargs["mutate_operations"][0]
        budget = operation.campaign_budget_operation.create
        self.assertEqual(budget.amount_micros, 30_000_000)
        self.assertEqual(budget.resource_name,
                         "customers/1234567890/campaignBudgets/-1")
        self.assertEqual(result["external_id"], "66")
        self.assertEqual(result["budget_resource"], "customers/1/campaignBudgets/55")
