# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.ac_ads_connector.models import ads_account as account_module
from odoo.addons.ac_ads_connector.tools.ads_api import AdsApiError
from odoo.addons.ac_ads_connector.tools.google_api import GoogleAdsApi

from .common import AdsCommon
from .test_audiences import FakeAudienceApi


class FakeConversionApi(FakeAudienceApi):
    conversions = []

    def upload_click_conversions(self, conversions):
        self._maybe_raise()
        type(self).conversions.append([dict(c) for c in conversions])


@tagged("post_install", "-at_install")
class TestConversions(AdsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.google_account.write({
            "state": "connected",
            "conversion_upload_enabled": True,
            "conversion_action_resource": "customers/1234567890/conversionActions/9",
        })
        cls.form = cls.env["ads.lead.form"].create({
            "name": "G Form", "account_id": cls.google_account.id,
            "external_id": "gf1"})
        cls.won_stage = cls.env["crm.stage"].search(
            [("is_won", "=", True)], limit=1)
        if not cls.won_stage:
            cls.won_stage = cls.env["crm.stage"].create(
                {"name": "Won", "is_won": True})

    def setUp(self):
        super().setUp()
        FakeConversionApi.conversions = []
        FakeConversionApi.raise_error = None
        patcher = patch.dict(account_module.PROVIDER_API,
                             {"google": FakeConversionApi,
                              "meta": FakeConversionApi})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _won_lead(self, gclid="GCLID123", email="win@example.com"):
        crm_lead = self.env["crm.lead"].create({
            "name": "Deal", "email_from": email, "expected_revenue": 500.0})
        self.env["ads.lead"].create({
            "account_id": self.google_account.id, "form_id": self.form.id,
            "external_id": f"ext-{gclid or 'nogclid'}", "gclid": gclid,
            "state": "processed", "crm_lead_id": crm_lead.id})
        crm_lead.write({"stage_id": self.won_stage.id})
        return crm_lead

    def test_won_lead_enqueues_once(self):
        crm_lead = self._won_lead()
        jobs = self.env["ads.push.job"].search(
            [("operation", "=", "conversion_upload")])
        self.assertEqual(len(jobs), 1)
        payload = jobs.payload
        self.assertEqual(payload["gclid"], "GCLID123")
        self.assertEqual(payload["value"], 500.0)
        # re-win: no duplicate
        crm_lead.write({"stage_id": self.won_stage.id})
        self.assertEqual(self.env["ads.push.job"].search_count(
            [("operation", "=", "conversion_upload")]), 1)
        # run it
        jobs._run_one()
        self.assertEqual(jobs.state, "done")
        self.assertEqual(FakeConversionApi.conversions[0][0]["gclid"], "GCLID123")
        self.assertEqual(jobs.result_external_id, "GCLID123")

    def test_enhanced_conversion_without_gclid(self):
        self._won_lead(gclid=False, email="Enhanced@Example.com")
        job = self.env["ads.push.job"].search(
            [("operation", "=", "conversion_upload")])
        job._run_one()
        self.assertEqual(job.state, "done")
        conversion = FakeConversionApi.conversions[0][0]
        self.assertFalse(conversion.get("gclid"))
        self.assertEqual(
            conversion["email_hash"],
            # sha256("enhanced@example.com")
            "3618c778b2628fda480e6999e5a35c6c78292058a00a74b563a6cd436fffc320")
        self.assertEqual(job.result_external_id, "enhanced")

    def test_toggle_off_no_job(self):
        self.google_account.conversion_upload_enabled = False
        self._won_lead()
        self.assertFalse(self.env["ads.push.job"].search(
            [("operation", "=", "conversion_upload")]))

    def test_no_contact_data_is_validation_error(self):
        crm_lead = self.env["crm.lead"].create({"name": "Bare"})
        self.env["ads.lead"].create({
            "account_id": self.google_account.id, "form_id": self.form.id,
            "external_id": "bare", "state": "processed",
            "crm_lead_id": crm_lead.id})
        crm_lead.write({"stage_id": self.won_stage.id})
        job = self.env["ads.push.job"].search(
            [("operation", "=", "conversion_upload")])
        job._run_one()
        self.assertEqual(job.state, "error")
        self.assertEqual(job.failure_type, "validation")

    def test_google_client_upload_payload(self):
        api = GoogleAdsApi(self.google_account)
        client = MagicMock()
        response = MagicMock()
        response.partial_failure_error = None
        client.get_service.return_value.upload_click_conversions.return_value = response
        requests_seen = {}

        def request_factory(**kwargs):
            requests_seen.update(kwargs)
            return MagicMock(**kwargs)

        def get_type(name):
            if name == "UploadClickConversionsRequest":
                return request_factory
            return MagicMock
        client.get_type.side_effect = get_type
        with patch.object(GoogleAdsApi, "_build_sdk", return_value=client):
            api.upload_click_conversions([{
                "gclid": "XYZ", "value": 42.0, "currency": "EUR",
                "datetime": "2026-08-10 10:00:00+00:00"}])
        self.assertEqual(requests_seen["customer_id"], "1234567890")
        self.assertTrue(requests_seen["partial_failure"])
        click = requests_seen["conversions"][0]
        self.assertEqual(click.gclid, "XYZ")
        self.assertEqual(click.conversion_value, 42.0)
        self.assertEqual(click.currency_code, "EUR")
        self.assertEqual(click.conversion_action,
                         "customers/1234567890/conversionActions/9")

    def test_google_client_requires_action(self):
        self.google_account.conversion_action_resource = False
        api = GoogleAdsApi(self.google_account)
        with patch.object(GoogleAdsApi, "_build_sdk", return_value=MagicMock()):
            with self.assertRaises(AdsApiError) as ctx:
                api.upload_click_conversions([{"gclid": "X", "datetime": "d"}])
        self.assertEqual(ctx.exception.failure_type, "validation")
