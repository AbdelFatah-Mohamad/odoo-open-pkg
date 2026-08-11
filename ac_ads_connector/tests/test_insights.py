# -*- coding: utf-8 -*-
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.ac_ads_connector.models import ads_account as account_module
from odoo.addons.ac_ads_connector.tools.ads_api import AdsApiError
from odoo.addons.ac_ads_connector.tools.google_api import GoogleAdsApi

from .common import AdsCommon
from .test_sync_engine import FakeStructureApi, _campaign_row


def _insight_row(ext_campaign, day, **kw):
    row = {
        "date": day, "campaign_external_id": ext_campaign,
        "adset_external_id": None, "ad_external_id": None,
        "spend": 10.0, "impressions": 1000, "clicks": 50, "reach": 800,
        "conversions": 2.0, "conversion_value": 40.0, "video_views": 5,
        "leads_count": 3,
    }
    row.update(kw)
    return row


class FakeInsightApi(FakeStructureApi):
    insight_rows = []
    windows = []
    raise_too_much_data = False
    job_status = "Job Completed"
    job_results = []

    def iter_insights(self, level, since, until, max_pages=None):
        type(self).windows.append((level, since, until))
        if self.raise_too_much_data:
            raise AdsApiError("too much", "too_much_data", code=100, subcode=1487534)
        if self.insight_rows:
            yield [dict(row) for row in self.insight_rows]

    def start_insights_job(self, level, since, until):
        return "RR-77"

    def poll_insights_job(self, report_run_id):
        return {"status": type(self).job_status, "percent": 100}

    def iter_insights_results(self, report_run_id, max_pages=None):
        if self.job_results:
            yield [dict(row) for row in self.job_results]


@tagged("post_install", "-at_install")
class TestInsights(AdsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = cls.env["ads.sync.engine"]
        cls.Insight = cls.env["ads.insight"]
        cls.meta_account.state = "connected"
        cls.campaign = cls.env["ads.campaign"].create({
            "title": "Insight Camp",
            "account_id": cls.meta_account.id,
            "external_id": "c1",
            "sync_status": "synced",
        })

    def setUp(self):
        super().setUp()
        FakeInsightApi.insight_rows = []
        FakeInsightApi.windows = []
        FakeInsightApi.raise_too_much_data = False
        FakeInsightApi.job_status = "Job Completed"
        FakeInsightApi.job_results = []

    # ------------------------------------------------------------------ upsert
    def test_upsert_and_restatement(self):
        day = date(2026, 8, 1)
        counts = self.Insight._upsert_from_rows(
            self.meta_account, "campaign", [_insight_row("c1", day)])
        self.assertEqual(counts["records_created"], 1)
        row = self.Insight.search([("campaign_id", "=", self.campaign.id)])
        self.assertEqual(row.spend, 10.0)
        # restatement: same key, new numbers -> same row updated
        counts = self.Insight._upsert_from_rows(
            self.meta_account, "campaign", [_insight_row("c1", day, spend=12.5)])
        self.assertEqual(counts["records_updated"], 1)
        rows = self.Insight.search([("campaign_id", "=", self.campaign.id)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.spend, 12.5)

    def test_upsert_skips_unknown_object(self):
        counts = self.Insight._upsert_from_rows(
            self.meta_account, "campaign",
            [_insight_row("ghost", date(2026, 8, 1))])
        self.assertEqual(counts["records_skipped"], 1)
        self.assertEqual(counts["records_created"], 0)

    def test_rates_computed(self):
        self.Insight._upsert_from_rows(
            self.meta_account, "campaign",
            [_insight_row("c1", date(2026, 8, 2), spend=20.0, clicks=40,
                          impressions=2000, leads_count=4)])
        row = self.Insight.search([("campaign_id", "=", self.campaign.id)])
        self.assertEqual(row.ctr, 2.0)
        self.assertEqual(row.cpc, 0.5)
        self.assertEqual(row.cpm, 10.0)
        self.assertEqual(row.cpl, 5.0)

    # ------------------------------------------------------------------- cron
    def test_cron_pulls_and_upserts(self):
        today = fields.Date.today()
        FakeInsightApi.insight_rows = [_insight_row("c1", today)]
        with patch.dict(account_module.PROVIDER_API, {"meta": FakeInsightApi}):
            self.engine._cron_sync_insights()
        self.assertTrue(self.Insight.search([("campaign_id", "=", self.campaign.id),
                                             ("date", "=", today)]))
        self.assertEqual(self.meta_account.insights_synced_until, today)
        # levels follow insight_sync_level=ad -> campaign+adset+ad windows
        levels = {w[0] for w in FakeInsightApi.windows}
        self.assertEqual(levels, {"campaign", "adset", "ad"})

    def test_too_much_data_escalates_to_async_job(self):
        FakeInsightApi.raise_too_much_data = True
        with patch.dict(account_module.PROVIDER_API, {"meta": FakeInsightApi}):
            self.engine._cron_sync_insights(today_only=True)
        pending = self.env["ads.sync.log"].search([
            ("account_id", "=", self.meta_account.id),
            ("async_report_ref", "=", "RR-77"),
            ("state", "=", "running")])
        self.assertEqual(len(pending), 3)  # one per level
        self.assertEqual(pending[0].payload_sample.get("level"), "ad")

    def test_async_job_polled_and_drained(self):
        today = fields.Date.today()
        self.env["ads.sync.log"].sudo().create({
            "account_id": self.meta_account.id, "job_type": "insights",
            "state": "running", "async_report_ref": "RR-77",
            "payload_sample": {"level": "campaign"}})
        FakeInsightApi.job_results = [_insight_row("c1", today, spend=33.0)]
        with patch.dict(account_module.PROVIDER_API, {"meta": FakeInsightApi}):
            self.engine._cron_sync_insights(today_only=True)
        row = self.Insight.search([("campaign_id", "=", self.campaign.id),
                                   ("date", "=", today)])
        self.assertEqual(row.spend, 33.0)
        job = self.env["ads.sync.log"].search([("async_report_ref", "=", "RR-77")])
        self.assertEqual(job.state, "done")

    def test_backfill_steps_and_retriggers(self):
        self.meta_account.write({"insight_backfill_days": 60,
                                 "insight_sync_level": "campaign"})
        with patch.dict(account_module.PROVIDER_API, {"meta": FakeInsightApi}), \
                patch("odoo.addons.base.models.ir_cron.IrCron._trigger") as trigger:
            self.engine._cron_sync_insights()
        today = fields.Date.today()
        first_cursor = self.meta_account.insight_backfill_date
        self.assertEqual(first_cursor, today - timedelta(days=35))  # 28 + 7
        self.assertTrue(trigger.called)
        with patch.dict(account_module.PROVIDER_API, {"meta": FakeInsightApi}), \
                patch("odoo.addons.base.models.ir_cron.IrCron._trigger"):
            self.engine._cron_sync_insights()
        self.assertEqual(self.meta_account.insight_backfill_date,
                         today - timedelta(days=42))

    # -------------------------------------------------------------- compaction
    def test_compaction_merges_and_preserves_totals(self):
        self.meta_account.insight_retention_days = 30
        old_month = (fields.Date.today() - timedelta(days=70)).replace(day=1)
        days = [old_month + timedelta(days=i) for i in range(3)]
        self.Insight._upsert_from_rows(
            self.meta_account, "campaign",
            [_insight_row("c1", d, spend=10.0, clicks=10) for d in days])
        count = self.Insight._compact_old_daily_rows(self.meta_account)
        self.assertEqual(count, 3)
        month_row = self.Insight.search([
            ("campaign_id", "=", self.campaign.id), ("granularity", "=", "month")])
        self.assertEqual(len(month_row), 1)
        self.assertEqual(month_row.spend, 30.0)
        self.assertEqual(month_row.clicks, 30)
        # a second wave of dailies for the SAME month merges into the row
        self.Insight._upsert_from_rows(
            self.meta_account, "campaign",
            [_insight_row("c1", old_month + timedelta(days=5), spend=5.0, clicks=5)])
        self.Insight._compact_old_daily_rows(self.meta_account)
        month_row = self.Insight.search([
            ("campaign_id", "=", self.campaign.id), ("granularity", "=", "month")])
        self.assertEqual(len(month_row), 1)
        self.assertEqual(month_row.spend, 35.0)
        self.assertFalse(self.Insight.search([
            ("campaign_id", "=", self.campaign.id), ("granularity", "=", "day"),
            ("date", "<", fields.Date.today() - timedelta(days=30))]))

    # -------------------------------------------------------------------- KPIs
    def test_campaign_kpi_rollup(self):
        today = fields.Date.today()
        self.Insight._upsert_from_rows(
            self.meta_account, "campaign",
            [_insight_row("c1", today - timedelta(days=1), spend=40.0,
                          leads_count=8, impressions=4000, clicks=100),
             _insight_row("c1", today - timedelta(days=40), spend=999.0)])
        campaign = self.campaign
        campaign.invalidate_recordset()
        self.assertEqual(campaign.spend_30d, 40.0)
        self.assertEqual(campaign.impressions_30d, 4000)
        self.assertEqual(campaign.clicks_30d, 100)
        self.assertEqual(campaign.insight_leads_30d, 8)
        self.assertEqual(campaign.cpl_30d, 5.0)

    # -------------------------------------------------- provider normalization
    def test_meta_insight_normalization(self):
        api = self.meta_account._get_api()
        data = {
            "date_start": "2026-08-01", "campaign_id": "c1", "spend": "12.34",
            "impressions": "1000", "clicks": "50", "reach": "900",
            "actions": [
                {"action_type": "lead", "value": "3"},
                {"action_type": "onsite_conversion.lead_grouped", "value": "1"},
                {"action_type": "purchase", "value": "2"},
                {"action_type": "video_view", "value": "7"},
                {"action_type": "link_click", "value": "40"},
            ],
            "action_values": [{"action_type": "purchase", "value": "199.9"}],
        }
        row = api._normalize_insight(data)
        self.assertEqual(row["date"], date(2026, 8, 1))
        self.assertEqual(row["spend"], 12.34)
        self.assertEqual(row["leads_count"], 4)
        self.assertEqual(row["conversions"], 2.0)
        self.assertEqual(row["conversion_value"], 199.9)
        self.assertEqual(row["video_views"], 7)

    def test_meta_split_on_too_much_data(self):
        api = self.meta_account._get_api()
        calls = []

        def fake_iter_edge(edge, fields_, params=None, max_pages=None, op="edge"):
            time_range = params["time_range"]
            since, until = time_range["since"], time_range["until"]
            if since != until:
                raise AdsApiError("too big", "too_much_data", code=100,
                                  subcode=1487534)
            calls.append(since)
            yield [{"date_start": since, "campaign_id": "c1", "spend": "1"}]

        with patch.object(type(api), "_iter_edge", side_effect=fake_iter_edge):
            pages = list(api.iter_insights("campaign", date(2026, 8, 1),
                                           date(2026, 8, 4)))
        self.assertEqual(sorted(calls),
                         ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"])
        self.assertEqual(len(pages), 4)

    def test_google_insight_query_and_normalization(self):
        api = GoogleAdsApi(self.google_account)
        query = api._q_insights("ad", date(2026, 8, 1), date(2026, 8, 4))
        self.assertIn("FROM ad_group_ad", query)
        self.assertIn("BETWEEN '2026-08-01' AND '2026-08-04'", query)
        self.assertIn("ad_group_ad.ad.id", query)

        row = SimpleNamespace(
            segments=SimpleNamespace(date="2026-08-02"),
            campaign=SimpleNamespace(id=42),
            ad_group=SimpleNamespace(id=77),
            ad_group_ad=SimpleNamespace(ad=SimpleNamespace(id=5)),
            metrics=SimpleNamespace(cost_micros=12_500_000, impressions=800,
                                    clicks=30, conversions=1.5,
                                    conversions_value=55.0, video_views=2))
        normalized = api._normalize_insight_row("ad", row)
        self.assertEqual(normalized["spend"], 12.5)
        self.assertEqual(normalized["campaign_external_id"], "42")
        self.assertEqual(normalized["ad_external_id"], "5")
        self.assertEqual(normalized["conversions"], 1.5)

    def test_sync_insights_button(self):
        FakeInsightApi.insight_rows = [_insight_row("c1", fields.Date.today())]
        with patch.dict(account_module.PROVIDER_API, {"meta": FakeInsightApi}), \
                patch("odoo.addons.base.models.ir_cron.IrCron._trigger"):
            result = self.meta_account.action_sync_insights()
        self.assertEqual(result["params"]["type"], "success")
