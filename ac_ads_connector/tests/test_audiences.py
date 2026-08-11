# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.ac_ads_connector.models import ads_account as account_module
from odoo.addons.ac_ads_connector.models.ads_push_job import AdsPushJob
from odoo.addons.ac_ads_connector.tools.google_api import GoogleAdsApi

from .common import AdsCommon
from .test_pushback_create import FakeCreateApi


def _audience_row(ext, name="Buyers"):
    return {"external_id": ext, "name": name, "description": "top buyers",
            "audience_type": "custom", "approximate_count": 1200,
            "platform_status": "Normal", "retention_days": 180,
            "remote_updated_at": None, "provider_data": {}, "raw": {"id": ext}}


class FakeAudienceApi(FakeCreateApi):
    audience_rows = []
    uploads = []

    def list_audiences(self, since=None, max_pages=None):
        if self.audience_rows:
            yield [dict(row) for row in self.audience_rows]

    def upload_audience_users(self, record, rows):
        type(self).uploads.append([dict(row) for row in rows])


@tagged("post_install", "-at_install")
class TestAudiences(AdsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = cls.env["ads.sync.engine"]
        cls.meta_account.state = "connected"

    def setUp(self):
        super().setUp()
        FakeAudienceApi.audience_rows = []
        FakeAudienceApi.uploads = []
        FakeAudienceApi.create_calls = []
        FakeAudienceApi.raise_error = None
        patcher = patch.dict(account_module.PROVIDER_API,
                             {"meta": FakeAudienceApi, "google": FakeAudienceApi})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pull_upserts_audiences(self):
        FakeAudienceApi.audience_rows = [_audience_row("au1")]
        self.engine._cron_sync_structure()
        audience = self.env["ads.audience"].search([("external_id", "=", "au1")])
        self.assertEqual(audience.name, "Buyers")
        self.assertEqual(audience.approximate_count, 1200)
        self.engine._cron_sync_structure()
        self.assertEqual(self.env["ads.audience"].search_count(
            [("external_id", "=", "au1")]), 1)

    def test_draft_create_flow(self):
        audience = self.env["ads.audience"].create({
            "name": "New List", "account_id": self.meta_account.id,
            "audience_type": "custom", "description": "vip"})
        self.assertEqual(audience.sync_status, "draft")
        audience.action_push()
        self.assertEqual(audience.external_id, "new-audience")
        self.assertEqual(audience.sync_status, "synced")
        object_type, values = FakeAudienceApi.create_calls[0]
        self.assertEqual(object_type, "audience")
        self.assertEqual(values["name"], "New List")

    def test_lookalike_creation_refused(self):
        audience = self.env["ads.audience"].create({
            "name": "LAL", "account_id": self.meta_account.id,
            "audience_type": "lookalike"})
        with self.assertRaises(UserError):
            audience.action_push()

    def test_upload_hashes_at_push_time_only(self):
        audience = self.env["ads.audience"].create({
            "name": "Uploads", "account_id": self.meta_account.id,
            "external_id": "au9", "sync_status": "synced"})
        partners = self.env["res.partner"].create([
            {"name": "P1", "email": "Test@Example.com", "phone": "+970591234567"},
            {"name": "P2", "email": "second@example.com"},
            {"name": "P3", "phone": "0599999999"},
            {"name": "P4"},  # no PII -> dropped
        ])
        wizard = self.env["ads.audience.upload.wizard"].create({
            "audience_id": audience.id, "partner_ids": [(6, 0, partners.ids)]})
        wizard.action_upload()
        job = self.env["ads.push.job"].search([
            ("res_model", "=", "ads.audience"), ("res_id", "=", audience.id)])
        self.assertEqual(job.state, "done")
        # payload carries ids, never hashes
        self.assertEqual(set(job.payload["partner_ids"]), set(partners.ids))
        self.assertNotIn("email", str(job.payload))
        rows = FakeAudienceApi.uploads[0]
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            rows[0]["email"],
            "973dfe463ec85785f5f95af5ba3906eedb2d931c24e69824a89ea65dba4e813b")
        self.assertEqual(job.result_external_id, "3 members")

    def test_upload_batching(self):
        audience = self.env["ads.audience"].create({
            "name": "Big", "account_id": self.meta_account.id,
            "external_id": "au10", "sync_status": "synced"})
        partners = self.env["res.partner"].create([
            {"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(3)])
        with patch.object(AdsPushJob, "AUDIENCE_BATCH", 2):
            wizard = self.env["ads.audience.upload.wizard"].create({
                "audience_id": audience.id, "partner_ids": [(6, 0, partners.ids)]})
            wizard.action_upload()
        self.assertEqual(len(FakeAudienceApi.uploads), 2)
        self.assertEqual([len(batch) for batch in FakeAudienceApi.uploads], [2, 1])

    def test_upload_requires_pushed_audience(self):
        audience = self.env["ads.audience"].create({
            "name": "Draft", "account_id": self.meta_account.id})
        with self.assertRaises(UserError):
            audience.action_open_upload_wizard()

    def test_google_user_list_normalization(self):
        api = GoogleAdsApi(self.google_account)
        row = SimpleNamespace(user_list=SimpleNamespace(
            id=31, name="CRM List", description="d", size_for_search=500,
            type_=SimpleNamespace(name="CRM_BASED"),
            resource_name="customers/1/userLists/31"))
        normalized = api._normalize_user_list(row)
        self.assertEqual(normalized["external_id"], "31")
        self.assertEqual(normalized["audience_type"], "user_list")
        self.assertEqual(normalized["approximate_count"], 500)
