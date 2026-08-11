# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields
from odoo.tests import tagged

from .common import AdsCommon
from .test_insights import _insight_row


@tagged("post_install", "-at_install")
class TestPerformanceReport(AdsCommon):

    def test_report_joins_spend_and_crm(self):
        campaign = self.env["ads.campaign"].create({
            "title": "Report Camp", "account_id": self.meta_account.id,
            "external_id": "rc1", "sync_status": "synced"})
        month = date(2026, 7, 1)
        self.env["ads.insight"]._upsert_from_rows(
            self.meta_account, "campaign",
            [_insight_row("rc1", month, spend=100.0, clicks=200, leads_count=10),
             _insight_row("rc1", month.replace(day=2), spend=50.0, clicks=100,
                          leads_count=5)])
        won_stage = self.env["crm.stage"].search([("is_won", "=", True)], limit=1) \
            or self.env["crm.stage"].create({"name": "Won", "is_won": True})
        utm = campaign.utm_campaign_id
        leads = self.env["crm.lead"].create([
            {"name": "L1", "campaign_id": utm.id, "expected_revenue": 400.0},
            {"name": "L2", "campaign_id": utm.id, "expected_revenue": 600.0},
        ])
        leads[1].stage_id = won_stage
        # crm side groups by create_date month (= today), insight by July -> the
        # spend row for July has no crm join; assert both months exist coherently
        self.env.flush_all()
        rows = self.env["ads.performance.report"].search(
            [("utm_campaign_id", "=", utm.id)])
        self.assertTrue(rows)
        july = rows.filtered(lambda r: r.month == month)
        self.assertEqual(july.spend, 150.0)
        self.assertEqual(july.clicks, 300)
        self.assertEqual(july.platform_leads, 15)
        # crm leads created today attach to the current month's row when spend
        # exists there; July's row must show zero crm leads
        self.assertEqual(july.crm_leads, 0)
        # add current-month spend so the crm join has a row to land on
        today = fields.Date.today()
        self.env["ads.insight"]._upsert_from_rows(
            self.meta_account, "campaign",
            [_insight_row("rc1", today, spend=80.0)])
        self.env.flush_all()
        current = self.env["ads.performance.report"].search([
            ("utm_campaign_id", "=", utm.id),
            ("month", "=", today.replace(day=1))])
        self.assertEqual(current.crm_leads, 2)
        self.assertEqual(current.expected_revenue, 1000.0)
        self.assertEqual(current.won_revenue, 600.0)
        self.assertEqual(current.cost_per_crm_lead, 40.0)
