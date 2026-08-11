# -*- coding: utf-8 -*-
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.ac_ads_connector.models import ads_account as account_module
from odoo.addons.ac_ads_connector.tools.google_api import GoogleAdsApi

from .common import AdsCommon


def _campaign_row(ext, name, **kw):
    row = {
        "external_id": ext, "name": name, "status": "enabled",
        "platform_status": "ACTIVE", "objective": "leads",
        "objective_raw": "OUTCOME_LEADS", "budget_type": "daily",
        "budget_amount": 50.0, "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "special_ad_categories": [], "start_datetime": datetime(2026, 8, 1),
        "stop_datetime": False, "remote_updated_at": datetime(2026, 8, 5, 10),
        "provider_data": {}, "raw": {"id": ext},
    }
    row.update(kw)
    return row


def _adset_row(ext, campaign_ext, name, **kw):
    row = {
        "external_id": ext, "campaign_external_id": campaign_ext, "name": name,
        "status": "paused", "platform_status": "PAUSED", "budget_type": "none",
        "budget_amount": 0.0, "optimization_goal": "LEAD_GENERATION",
        "billing_event": "IMPRESSIONS", "targeting": {"geo_locations": {}},
        "promoted_object": None, "start_datetime": False, "stop_datetime": False,
        "remote_updated_at": datetime(2026, 8, 5, 10), "provider_data": {},
        "raw": {"id": ext},
    }
    row.update(kw)
    return row


def _ad_row(ext, adset_ext, name, **kw):
    row = {
        "external_id": ext, "adset_external_id": adset_ext, "name": name,
        "status": "enabled", "platform_status": "ACTIVE",
        "preview_url": "https://fb.me/x", "final_urls": [],
        "creative": {"external_id": f"cr-{ext}", "name": "Creative",
                     "title": "Buy now", "body": "Great stuff",
                     "call_to_action": "SHOP_NOW", "image_url": "https://img/1.jpg",
                     "thumbnail_url": None, "object_story_spec": {}, "raw": {}},
        "remote_updated_at": datetime(2026, 8, 5, 10), "provider_data": {},
        "raw": {"id": ext},
    }
    row.update(kw)
    return row


class FakeStructureApi:
    """Stands in for MetaAdsApi/GoogleAdsApi in engine tests: same generator
    contract (pages of normalized dicts), zero SDK."""

    campaign_rows = []
    adset_rows = []
    ad_rows = []

    def __init__(self, account):
        self.account = account

    def list_campaigns(self, since=None, max_pages=None):
        if self.campaign_rows:
            yield list(self.campaign_rows)

    def list_adsets(self, since=None, max_pages=None):
        if self.adset_rows:
            yield list(self.adset_rows)

    def list_ads(self, since=None, max_pages=None):
        if self.ad_rows:
            yield list(self.ad_rows)


@tagged("post_install", "-at_install")
class TestSyncEngine(AdsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = cls.env["ads.sync.engine"]
        cls.meta_account.state = "connected"

    def _run_sync(self, campaigns=None, adsets=None, ads=None):
        FakeStructureApi.campaign_rows = campaigns or []
        FakeStructureApi.adset_rows = adsets or []
        FakeStructureApi.ad_rows = ads or []
        with patch.dict(account_module.PROVIDER_API, {"meta": FakeStructureApi}):
            self.engine._cron_sync_structure()

    def _default_fixture(self):
        return dict(
            campaigns=[_campaign_row("c1", "Summer Sale"),
                       _campaign_row("c2", "Winter Push", status="paused",
                                     platform_status="PAUSED")],
            adsets=[_adset_row("s1", "c1", "Leads PS"),
                    _adset_row("s9", "missing", "Orphan")],
            ads=[_ad_row("a1", "s1", "Video A")],
        )

    # ------------------------------------------------------------- happy path
    def test_sync_creates_hierarchy(self):
        self._run_sync(**self._default_fixture())
        Campaign = self.env["ads.campaign"]
        campaign = Campaign.search([("account_id", "=", self.meta_account.id),
                                    ("external_id", "=", "c1")])
        self.assertEqual(campaign.title, "Summer Sale")
        self.assertEqual(campaign.status, "enabled")
        self.assertEqual(campaign.budget_amount, 50.0)
        self.assertEqual(campaign.sync_status, "synced")
        # utm delegation
        self.assertTrue(campaign.utm_campaign_id)
        self.assertTrue(campaign.is_auto_campaign)
        self.assertEqual(campaign.utm_campaign_id.title, "Summer Sale")
        # children
        adset = self.env["ads.adset"].search([("external_id", "=", "s1")])
        self.assertEqual(adset.campaign_id, campaign)
        ad = self.env["ads.ad"].search([("external_id", "=", "a1")])
        self.assertEqual(ad.adset_id, adset)
        self.assertEqual(ad.campaign_id, campaign)
        self.assertEqual(ad.creative_id.title, "Buy now")
        # orphan adset skipped, not created
        self.assertFalse(self.env["ads.adset"].search([("external_id", "=", "s9")]))
        # watermark + log (orphan is a skip, not a failure -> state stays done)
        self.assertTrue(self.meta_account.struct_synced_at)
        self.assertFalse(self.meta_account.struct_cursor)
        log = self.env["ads.sync.log"].search(
            [("account_id", "=", self.meta_account.id)], order="id desc", limit=1)
        self.assertEqual(log.job_type, "structure")
        self.assertEqual(log.state, "done")
        self.assertGreaterEqual(log.records_skipped, 1)

    def test_sync_log_state_done_when_no_failures(self):
        fixture = self._default_fixture()
        fixture["adsets"] = [_adset_row("s1", "c1", "Leads PS")]
        self._run_sync(**fixture)
        log = self.env["ads.sync.log"].search(
            [("account_id", "=", self.meta_account.id)], limit=1, order="id desc")
        self.assertEqual(log.state, "done")
        self.assertEqual(log.records_created, 4)  # 2 campaigns + 1 adset + 1 ad

    def test_sync_idempotent(self):
        fixture = self._default_fixture()
        self._run_sync(**fixture)
        counts_before = (
            self.env["ads.campaign"].search_count([]),
            self.env["ads.adset"].search_count([]),
            self.env["ads.ad"].search_count([]),
            self.env["ads.creative"].search_count([]),
        )
        self._run_sync(**fixture)
        counts_after = (
            self.env["ads.campaign"].search_count([]),
            self.env["ads.adset"].search_count([]),
            self.env["ads.ad"].search_count([]),
            self.env["ads.creative"].search_count([]),
        )
        self.assertEqual(counts_before, counts_after)
        log = self.env["ads.sync.log"].search(
            [("account_id", "=", self.meta_account.id)], order="id desc", limit=1)
        self.assertEqual(log.records_created, 0)

    def test_remote_update_applied_when_local_clean(self):
        self._run_sync(campaigns=[_campaign_row("c1", "Summer Sale")])
        self._run_sync(campaigns=[_campaign_row("c1", "Summer Sale v2",
                                                budget_amount=75.0)])
        campaign = self.env["ads.campaign"].search([("external_id", "=", "c1")])
        self.assertEqual(campaign.title, "Summer Sale v2")
        self.assertEqual(campaign.budget_amount, 75.0)

    # --------------------------------------------------------------- guards
    def test_local_pending_not_clobbered(self):
        self._run_sync(campaigns=[_campaign_row("c1", "Summer Sale")])
        campaign = self.env["ads.campaign"].search([("external_id", "=", "c1")])
        campaign.write({"sync_status": "local_changes"})
        campaign.with_context(skip_ads_sync=True).write({"title": "My Local Edit"})
        self._run_sync(campaigns=[_campaign_row("c1", "Remote Rename",
                                                budget_amount=99.0)])
        self.assertEqual(campaign.title, "My Local Edit")
        self.assertEqual(campaign.budget_amount, 50.0)
        self.assertTrue(campaign.remote_snapshot)
        self.assertIn("Remote Rename", str(campaign.remote_snapshot))

    def test_poison_row_isolated(self):
        rows = [_campaign_row("c1", "Good"),
                {"name": "no external id"},  # missing external_id -> KeyError
                _campaign_row("c3", "Also Good")]
        counts = self.engine._upsert_page(self.meta_account, "ads.campaign", rows)
        self.assertEqual(counts["records_created"], 2)
        self.assertEqual(counts["records_failed"], 1)
        self.assertTrue(self.env["ads.campaign"].search([("external_id", "=", "c3")]))

    def test_account_isolation(self):
        """A crashing account must not prevent the next one from syncing."""
        broken = self.meta_account
        healthy = self.meta_account.copy({
            "name": "Meta Healthy", "external_account_id": "555"})
        healthy.write({"state": "connected",
                       "meta_access_token": "tok-CCCCCCCCCCCCCCCCCCCCCCCC"})

        class ExplodingApi(FakeStructureApi):
            def list_campaigns(self, since=None, max_pages=None):
                if self.account == broken:
                    raise ValueError("boom")
                yield [_campaign_row("h1", "Healthy Campaign")]

        with patch.dict(account_module.PROVIDER_API, {"meta": ExplodingApi}):
            self.engine._cron_sync_structure()
        self.assertTrue(self.env["ads.campaign"].search(
            [("account_id", "=", healthy.id), ("external_id", "=", "h1")]))
        logs = {log.account_id: log.state for log in self.env["ads.sync.log"].search(
            [("job_type", "=", "structure")])}
        self.assertEqual(logs[broken], "error")
        self.assertEqual(logs[healthy], "done")

    # ------------------------------------------------------------------- utm
    def test_utm_identifier_dedup_across_accounts(self):
        other = self.meta_account.copy({"name": "Meta B", "external_account_id": "888"})
        self.engine._upsert_page(self.meta_account, "ads.campaign",
                                 [_campaign_row("c1", "Same Name")])
        self.engine._upsert_page(other, "ads.campaign",
                                 [_campaign_row("cX", "Same Name")])
        campaigns = self.env["ads.campaign"].search([("title", "=", "Same Name")])
        self.assertEqual(len(campaigns), 2)
        names = campaigns.mapped("name")
        self.assertEqual(len(set(names)), 2, "utm identifiers must stay unique")

    def test_unlink_keeps_utm_when_lead_references(self):
        self._run_sync(campaigns=[_campaign_row("c1", "Keep Me"),
                                  _campaign_row("c2", "Delete Me")])
        keep, delete = (self.env["ads.campaign"].search([("external_id", "=", ext)])
                        for ext in ("c1", "c2"))
        utm_keep, utm_delete = keep.utm_campaign_id, delete.utm_campaign_id
        self.env["crm.lead"].create({"name": "From ad", "campaign_id": utm_keep.id})
        (keep + delete).unlink()
        self.assertTrue(utm_keep.exists())
        self.assertFalse(utm_keep.active)
        self.assertFalse(utm_delete.exists())

    # ------------------------------------------------- provider normalization
    def test_google_row_normalization(self):
        api = GoogleAdsApi(self.google_account)
        enum = lambda name: SimpleNamespace(name=name)  # noqa: E731
        campaign_row = SimpleNamespace(
            campaign=SimpleNamespace(
                id=42, name="Search PS", status=enum("ENABLED"),
                advertising_channel_type=enum("SEARCH"),
                start_date="2026-08-01", end_date="",
                bidding_strategy_type=enum("TARGET_SPEND"),
                campaign_budget="customers/1/campaignBudgets/7",
                resource_name="customers/1/campaigns/42"),
            campaign_budget=SimpleNamespace(amount_micros=25_000_000,
                                            total_amount_micros=0))
        row = api._normalize_campaign(campaign_row)
        self.assertEqual(row["external_id"], "42")
        self.assertEqual(row["status"], "enabled")
        self.assertEqual(row["objective"], "traffic")
        self.assertEqual(row["budget_amount"], 25.0)
        self.assertEqual(row["google_budget_resource"], "customers/1/campaignBudgets/7")

        ad_group_row = SimpleNamespace(ad_group=SimpleNamespace(
            id=77, name="AG", status=enum("PAUSED"), type_=enum("SEARCH_STANDARD"),
            campaign="customers/1/campaigns/42", cpc_bid_micros=1_500_000,
            resource_name="customers/1/adGroups/77"))
        row = api._normalize_ad_group(ad_group_row)
        self.assertEqual(row["campaign_external_id"], "42")
        self.assertEqual(row["cpc_bid"], 1.5)
        self.assertEqual(row["status"], "paused")

        ad_row = SimpleNamespace(ad_group_ad=SimpleNamespace(
            status=enum("ENABLED"), ad_group="customers/1/adGroups/77",
            resource_name="customers/1/adGroupAds/77~5",
            ad=SimpleNamespace(id=5, name="", type_=enum("RESPONSIVE_SEARCH_AD"),
                               final_urls=["https://alshayeb.ps"])))
        row = api._normalize_ad(ad_row)
        self.assertEqual(row["adset_external_id"], "77")
        self.assertEqual(row["name"], "Ad 5")
        self.assertEqual(row["final_urls"], ["https://alshayeb.ps"])

    def test_meta_raw_normalization(self):
        api = self.meta_account._get_api()
        data = {"id": "120", "name": "FB Camp", "status": "ACTIVE",
                "effective_status": "ACTIVE", "objective": "OUTCOME_LEADS",
                "daily_budget": "5000", "special_ad_categories": ["NONE"],
                "start_time": "2026-08-01T00:00:00+0300",
                "updated_time": "2026-08-05T12:00:00+0300"}
        row = api._normalize_campaign(data)
        self.assertEqual(row["external_id"], "120")
        self.assertEqual(row["status"], "enabled")
        self.assertEqual(row["objective"], "leads")
        self.assertEqual(row["budget_type"], "daily")
        self.assertEqual(row["budget_amount"], 50.0)
        self.assertEqual(row["remote_updated_at"].hour, 9)  # UTC-naive

    def test_sync_now_notification(self):
        FakeStructureApi.campaign_rows = [_campaign_row("c1", "Manual")]
        FakeStructureApi.adset_rows = FakeStructureApi.ad_rows = []
        with patch.dict(account_module.PROVIDER_API, {"meta": FakeStructureApi}):
            result = self.meta_account.action_sync_structure()
        self.assertEqual(result["params"]["type"], "success")
        self.assertIn("1 created", result["params"]["message"])
