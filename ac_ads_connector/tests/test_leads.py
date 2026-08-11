# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.ac_ads_connector.models import ads_account as account_module
from odoo.addons.ac_ads_connector.tools.google_api import GoogleAdsApi

from .common import AdsCommon
from .test_sync_engine import FakeStructureApi, _campaign_row

QUESTIONS = [
    {"key": "email", "label": "Email"},
    {"key": "full_name", "label": "Full name"},
    {"key": "phone_number", "label": "Phone"},
    {"key": "city", "label": "City"},
    {"key": "country", "label": "Country"},
    {"key": "budget_range", "label": "What is your budget?"},
]


def _form_row(ext="f1", name="PS Leads"):
    return {"external_id": ext, "name": name, "status": "ACTIVE", "locale": "ar_AR",
            "questions": QUESTIONS, "page_external_id": "424242"}


def _lead_row(ext, form_ext="f1", created=None, email="lead@example.com",
              phone="0599123456", **kw):
    row = {
        "external_id": ext,
        "form_external_id": form_ext,
        "created_time": created or datetime(2026, 8, 9, 10, 0),
        "campaign_external_id": "c1",
        "adset_external_id": None,
        "ad_external_id": None,
        "is_organic": False,
        "gclid": None,
        "fields": [
            {"key": "email", "values": [email]},
            {"key": "full_name", "values": ["Omar Test"]},
            {"key": "phone_number", "values": [phone]},
            {"key": "city", "values": ["Ramallah"]},
            {"key": "country", "values": ["State of Palestine"]},
            {"key": "budget_range", "values": ["1000-2000 USD"]},
        ],
        "raw": {"id": ext},
    }
    row.update(kw)
    return row


class FakeLeadApi(FakeStructureApi):
    LEADS_PER_FORM = True
    form_rows = []
    lead_rows = []
    lead_calls = []

    def list_lead_forms(self):
        return [dict(row) for row in self.form_rows]

    def iter_leads(self, form_external_id=None, since=None, max_pages=None):
        type(self).lead_calls.append((form_external_id, since))
        rows = [dict(row) for row in self.lead_rows
                if form_external_id in (None, row["form_external_id"])]
        if rows:
            yield rows


class FakeGoogleLeadApi(FakeLeadApi):
    LEADS_PER_FORM = False


@tagged("post_install", "-at_install")
class TestLeads(AdsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = cls.env["ads.sync.engine"]
        cls.Lead = cls.env["ads.lead"]
        cls.meta_account.state = "connected"
        cls.meta_account.company_id.country_id = cls.env.ref("base.ps")
        cls.campaign = cls.env["ads.campaign"].create({
            "title": "Lead Camp", "account_id": cls.meta_account.id,
            "external_id": "c1", "sync_status": "synced"})
        cls.form = cls.env["ads.lead.form"]._upsert_from_row(
            cls.meta_account, _form_row())

    def setUp(self):
        super().setUp()
        FakeLeadApi.form_rows = []
        FakeLeadApi.lead_rows = []
        FakeLeadApi.lead_calls = []

    def _run_lead_cron(self, api_cls=FakeLeadApi):
        with patch.dict(account_module.PROVIDER_API, {"meta": api_cls,
                                                      "google": api_cls}):
            self.engine._cron_sync_leads()

    # ------------------------------------------------------------------- forms
    def test_form_upsert_generates_default_mappings(self):
        mappings = {m.question_key: m for m in self.form.mapping_ids}
        self.assertEqual(mappings["email"].crm_field_id.name, "email_from")
        self.assertEqual(mappings["full_name"].crm_field_id.name, "contact_name")
        self.assertEqual(mappings["phone_number"].crm_field_id.name, "phone")
        self.assertEqual(mappings["city"].crm_field_id.name, "city")
        self.assertEqual(mappings["country"].crm_field_id.name, "country_id")
        self.assertFalse(mappings["budget_range"].crm_field_id)
        # idempotent: re-upsert does not duplicate mapping lines
        self.env["ads.lead.form"]._upsert_from_row(self.meta_account, _form_row())
        self.assertEqual(len(self.form.mapping_ids), len(QUESTIONS))

    # ------------------------------------------------------------------ upsert
    def test_lead_upsert_idempotent(self):
        created, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("l1"))
        self.assertTrue(created)
        again, same = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("l1"))
        self.assertFalse(again)
        self.assertEqual(record, same)
        self.assertEqual(record.email, "lead@example.com")
        self.assertEqual(record.contact_name, "Omar Test")
        self.assertEqual(record.campaign_id, self.campaign)

    def test_attribution_resolves_late_campaign(self):
        _created, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("l2", campaign_external_id="c9"))
        self.assertFalse(record.campaign_id)
        self.engine._upsert_page(self.meta_account, "ads.campaign",
                                 [_campaign_row("c9", "Late Campaign")])
        record._resolve_attribution()
        self.assertEqual(record.campaign_id.external_id, "c9")

    # -------------------------------------------------------------- processing
    def test_process_creates_crm_lead_with_mapping_and_utm(self):
        _created, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("l3"))
        record.action_process()
        self.assertEqual(record.state, "processed")
        lead = record.crm_lead_id
        self.assertEqual(lead.email_from, "lead@example.com")
        self.assertEqual(lead.contact_name, "Omar Test")
        self.assertEqual(lead.city, "Ramallah")
        self.assertEqual(lead.country_id, self.env.ref("base.ps"))
        self.assertIn("What is your budget?: 1000-2000 USD", lead.description)
        # UTM chain
        self.assertEqual(lead.campaign_id, self.campaign.utm_campaign_id)
        self.assertEqual(lead.medium_id, self.meta_account.lead_medium_id)
        self.assertTrue(lead.source_id)

    def test_team_fallback_form_over_account(self):
        team_a = self.env["crm.team"].create({"name": "Team A"})
        team_b = self.env["crm.team"].create({"name": "Team B"})
        self.meta_account.lead_team_id = team_a
        self.form.team_id = team_b
        _created, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("l4"))
        record.action_process()
        self.assertEqual(record.crm_lead_id.team_id, team_b)

    def test_duplicate_policies(self):
        existing = self.env["crm.lead"].create({
            "name": "Existing", "email_from": "dup@example.com"})
        # log_on_existing (default)
        _c, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("d1", email="dup@example.com"))
        record.action_process()
        self.assertEqual(record.state, "merged")
        self.assertEqual(record.crm_lead_id, existing)
        self.assertTrue(any("New ad lead submission" in (m.body or "")
                            for m in existing.message_ids))
        # skip
        self.meta_account.duplicate_policy = "skip"
        _c, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("d2", email="dup@example.com"))
        record.action_process()
        self.assertEqual(record.state, "skipped")
        self.assertFalse(record.crm_lead_id)
        self.assertEqual(record.duplicate_lead_id, existing)
        # create_anyway
        self.meta_account.duplicate_policy = "create_anyway"
        _c, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("d3", email="dup@example.com"))
        record.action_process()
        self.assertEqual(record.state, "processed")
        self.assertNotEqual(record.crm_lead_id, existing)
        # dedup off entirely
        self.meta_account.duplicate_policy = "skip"
        self.meta_account.lead_dedup_mode = "none"
        _c, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("d4", email="dup@example.com"))
        record.action_process()
        self.assertEqual(record.state, "processed")

    def test_phone_dedup(self):
        existing = self.env["crm.lead"].create({
            "name": "Phone Lead", "phone": "+970599999999"})
        self.assertTrue(existing.phone_sanitized)
        _c, record = self.Lead._upsert_from_row(
            self.meta_account, self.form,
            _lead_row("p1", email="other@example.com", phone="0599999999"))
        record.action_process()
        self.assertEqual(record.state, "merged")
        self.assertEqual(record.duplicate_lead_id, existing)

    def test_force_reprocess_error_rows(self):
        _c, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("e1"))
        record.write({"state": "error", "error_message": "boom"})
        record.action_process()  # without force: untouched
        self.assertEqual(record.state, "error")
        wizard = self.env["ads.lead.process.wizard"].create({
            "lead_ids": [(6, 0, record.ids)], "force_reprocess": True})
        wizard.action_process()
        self.assertEqual(record.state, "processed")

    def test_process_pending_respects_auto_convert(self):
        self.meta_account.lead_auto_convert = False
        _c, record = self.Lead._upsert_from_row(
            self.meta_account, self.form, _lead_row("a1"))
        self.Lead._process_pending()
        self.assertEqual(record.state, "new")
        self.meta_account.lead_auto_convert = True
        self.Lead._process_pending()
        self.assertEqual(record.state, "processed")

    # ------------------------------------------------------------------- crons
    def test_cron_pulls_and_processes(self):
        FakeLeadApi.form_rows = [_form_row()]
        FakeLeadApi.lead_rows = [_lead_row("n1", created=datetime(2026, 8, 9, 8)),
                                 _lead_row("n2", created=datetime(2026, 8, 9, 9),
                                           email="second@example.com")]
        self.meta_account.forms_synced_at = False
        self._run_lead_cron()
        rows = self.Lead.search([("account_id", "=", self.meta_account.id),
                                 ("external_id", "in", ("n1", "n2"))])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r.state in ("processed", "merged") for r in rows))
        self.assertEqual(self.form.leads_synced_until, datetime(2026, 8, 9, 9))
        # second run with same payloads: watermark overlap, zero new rows
        self._run_lead_cron()
        rows = self.Lead.search([("external_id", "in", ("n1", "n2"))])
        self.assertEqual(len(rows), 2)
        # the since passed on the 2nd run is watermark - 1 day
        last_since = FakeLeadApi.lead_calls[-1][1]
        self.assertEqual(last_since, datetime(2026, 8, 8, 9))

    def test_google_routing_single_query(self):
        self.google_account.write({"state": "connected"})
        google_form = self.env["ads.lead.form"]._upsert_from_row(
            self.google_account, _form_row(ext="gf1", name="Google Form"))
        FakeGoogleLeadApi.form_rows = [_form_row(ext="gf1", name="Google Form")]
        FakeGoogleLeadApi.lead_rows = [
            _lead_row("g1", form_ext="gf1", email="g1@example.com"),
            _lead_row("g9", form_ext="unknown-form", email="g9@example.com"),
        ]
        job = self.env["ads.sync.log"]._job_start(self.google_account, "leads")
        with patch.dict(account_module.PROVIDER_API, {"google": FakeGoogleLeadApi}):
            counters = self.engine._sync_leads_account(self.google_account, job)
        self.assertEqual(counters["records_created"], 1)
        self.assertEqual(counters["records_skipped"], 1)
        self.assertTrue(self.Lead.search([("form_id", "=", google_form.id)]))
        # single query: exactly one iter_leads call, no per-form fan-out
        self.assertEqual(len(FakeGoogleLeadApi.lead_calls), 1)
        self.assertIsNone(FakeGoogleLeadApi.lead_calls[0][0])

    def test_lead_staleness_alert(self):
        self.env["ads.sync.log"].sudo().create({
            "account_id": self.meta_account.id, "job_type": "leads",
            "state": "done",
            "started_at": fields.Datetime.now() - timedelta(days=2)})
        self.meta_account._check_lead_sync_health()
        activity = self.env["mail.activity"].search([
            ("res_model", "=", "ads.account"),
            ("res_id", "=", self.meta_account.id)])
        self.assertTrue(activity)
        self.assertIn("24", activity[0].summary)

    # -------------------------------------------------- provider normalization
    def test_meta_lead_normalization(self):
        api = self.meta_account._get_api()
        data = {"id": "L1", "created_time": "2026-08-09T08:30:00+0000",
                "ad_id": "a1", "adset_id": "s1", "campaign_id": "c1",
                "form_id": "f1", "is_organic": False,
                "field_data": [{"name": "EMAIL", "values": ["x@y.z"]},
                               {"name": "full_name", "values": ["A B"]}]}
        row = api._normalize_lead(data)
        self.assertEqual(row["external_id"], "L1")
        self.assertEqual(row["form_external_id"], "f1")
        self.assertEqual(row["fields"][0], {"key": "email", "values": ["x@y.z"]})
        self.assertEqual(row["created_time"].hour, 8)

    def test_google_lead_normalization(self):
        api = GoogleAdsApi(self.google_account)
        enum = SimpleNamespace(name="EMAIL")
        data = SimpleNamespace(
            id=555, asset="customers/1/assets/gf1",
            campaign="customers/1/campaigns/42",
            ad_group="customers/1/adGroups/77", gclid="XYZ",
            submission_date_time="2026-08-09 11:22:33",
            lead_form_submission_fields=[
                SimpleNamespace(field_type=enum, field_value="g@x.co")],
            custom_lead_form_submission_fields=[
                SimpleNamespace(question_text="Budget?", field_value="500")])
        row = api._normalize_lead(SimpleNamespace(lead_form_submission_data=data))
        self.assertEqual(row["external_id"], "555")
        self.assertEqual(row["form_external_id"], "gf1")
        self.assertEqual(row["campaign_external_id"], "42")
        self.assertEqual(row["gclid"], "XYZ")
        self.assertEqual(row["fields"][0], {"key": "email", "values": ["g@x.co"]})
        self.assertEqual(row["fields"][1], {"key": "budget?", "values": ["500"]})
