# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.ac_ads_connector.models import ads_account as account_module

from .common import AdsCommon
from .test_conversions import FakeConversionApi


@tagged("post_install", "-at_install")
class TestClickIds(AdsCommon):

    # ------------------------------------------------------------- cookie chain
    def test_tracking_fields_untouched(self):
        """Deliberate design: gclid/fbclid must NOT be in the shared
        tracking_fields() — link_tracker.create() force-sets every listed
        field on link.tracker and would crash on unknown columns."""
        params = [entry[0] for entry in self.env["utm.mixin"].tracking_fields()]
        self.assertNotIn("gclid", params)
        self.assertNotIn("fbclid", params)

    def test_default_get_fills_click_ids_from_cookies(self):
        fake_request = MagicMock()
        fake_request.cookies = {
            "odoo_gclid": "GCLID-COOKIE",
            "odoo_fbclid": "FBCLID-COOKIE",
        }
        with patch("odoo.addons.ac_ads_connector.models.crm_lead.request",
                   fake_request):
            values = self.env["crm.lead"].sudo().default_get(["gclid", "fbclid"])
        self.assertEqual(values.get("gclid"), "GCLID-COOKIE")
        self.assertEqual(values.get("fbclid"), "FBCLID-COOKIE")
        # salesman path: own browsing cookies are not attribution
        salesman = self.env["res.users"].create({
            "name": "Sales", "login": "clickid_sales",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("sales_team.group_sale_salesman").id])]})
        with patch("odoo.addons.ac_ads_connector.models.crm_lead.request",
                   fake_request):
            values = self.env["crm.lead"].with_user(salesman).default_get(
                ["gclid", "fbclid"])
        self.assertFalse(values.get("gclid"))

    # ---------------------------------------------------------- url tagging
    def test_utm_tag_url(self):
        campaign = self.env["ads.campaign"].create({
            "title": "Tag Camp", "account_id": self.meta_account.id,
            "external_id": "tc1", "sync_status": "synced"})
        tagged_url = campaign._utm_tag_url("https://alshayeb.ps/promo?ref=x")
        self.assertIn("ref=x", tagged_url)
        self.assertIn("utm_medium=", tagged_url)
        self.assertIn("utm_campaign=", tagged_url)
        self.assertIn(campaign.utm_campaign_id.name.replace(" ", "+"), tagged_url)
        # existing utm params are respected, never overwritten
        manual = campaign._utm_tag_url("https://x.ps/?utm_campaign=manual")
        self.assertIn("utm_campaign=manual", manual)
        self.assertEqual(manual.count("utm_campaign="), 1)
        # idempotent
        self.assertEqual(campaign._utm_tag_url(tagged_url), tagged_url)
        self.assertFalse(campaign._utm_tag_url(""))

    def test_creative_link_tagged_on_draft_push_payload(self):
        campaign = self.env["ads.campaign"].create({
            "title": "Creative Camp", "account_id": self.meta_account.id,
            "external_id": "cc9", "sync_status": "synced"})
        adset = self.env["ads.adset"].create({
            "name": "Set", "campaign_id": campaign.id, "external_id": "s9",
            "sync_status": "synced"})
        creative = self.env["ads.creative"].create({
            "name": "OSS", "account_id": self.meta_account.id,
            "object_story_spec": {"page_id": "424242",
                                  "link_data": {"link": "https://alshayeb.ps/lp"}}})
        ad = self.env["ads.ad"].create({
            "name": "Ad", "adset_id": adset.id, "creative_id": creative.id,
            "sync_status": "draft"})
        payload = ad._create_push_payload()
        link = payload["values"]["creative"]["object_story_spec"]["link_data"]["link"]
        self.assertIn("utm_campaign=", link)
        # the stored creative record itself is untouched
        self.assertEqual(creative.object_story_spec["link_data"]["link"],
                         "https://alshayeb.ps/lp")

    # ------------------------------------------------ website-lead conversions
    def test_website_lead_with_gclid_uploads_conversion(self):
        self.google_account.write({
            "state": "connected",
            "conversion_upload_enabled": True,
            "conversion_action_resource": "customers/1234567890/conversionActions/9"})
        won_stage = self.env["crm.stage"].search([("is_won", "=", True)], limit=1) \
            or self.env["crm.stage"].create({"name": "Won", "is_won": True})
        lead = self.env["crm.lead"].create({
            "name": "Website deal", "gclid": "WEB-GCLID",
            "expected_revenue": 250.0})
        self.assertFalse(lead.ads_lead_ids)
        FakeConversionApi.conversions = []
        FakeConversionApi.raise_error = None
        with patch.dict(account_module.PROVIDER_API,
                        {"google": FakeConversionApi, "meta": FakeConversionApi}):
            lead.write({"stage_id": won_stage.id})
            job = self.env["ads.push.job"].search([
                ("res_model", "=", "crm.lead"), ("res_id", "=", lead.id),
                ("operation", "=", "conversion_upload")])
            self.assertEqual(len(job), 1)
            self.assertEqual(job.payload["gclid"], "WEB-GCLID")
            self.assertEqual(job.payload["value"], 250.0)
            job._run_one()
        self.assertEqual(job.state, "done")
        self.assertEqual(FakeConversionApi.conversions[0][0]["gclid"], "WEB-GCLID")
        # re-win: idempotent
        lead.write({"stage_id": won_stage.id})
        self.assertEqual(self.env["ads.push.job"].search_count([
            ("res_model", "=", "crm.lead"), ("res_id", "=", lead.id)]), 1)

    def test_no_gclid_no_account_no_job(self):
        won_stage = self.env["crm.stage"].search([("is_won", "=", True)], limit=1) \
            or self.env["crm.stage"].create({"name": "Won", "is_won": True})
        lead = self.env["crm.lead"].create({"name": "Plain deal"})
        lead.write({"stage_id": won_stage.id})
        self.assertFalse(self.env["ads.push.job"].search([
            ("res_model", "=", "crm.lead"), ("res_id", "=", lead.id)]))

    def test_staged_lead_copies_gclid_to_crm(self):
        self.google_account.state = "connected"
        form = self.env["ads.lead.form"].create({
            "name": "GF", "account_id": self.google_account.id,
            "external_id": "gf9"})
        _created, staged = self.env["ads.lead"]._upsert_from_row(
            self.google_account, form, {
                "external_id": "gl9", "created_time": False, "gclid": "STAGE-GCLID",
                "fields": [{"key": "email", "values": ["g9@example.com"]}],
                "raw": {}})
        staged.action_process()
        self.assertEqual(staged.crm_lead_id.gclid, "STAGE-GCLID")
