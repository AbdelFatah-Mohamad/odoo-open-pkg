# -*- coding: utf-8 -*-
import hashlib
import hmac
import json
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.ac_ads_connector.models import ads_account as account_module

from .test_leads import FakeLeadApi, _form_row, _lead_row


@tagged("post_install", "-at_install")
class TestWebhooks(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.meta_account = cls.env["ads.account"].create({
            "name": "Meta WH", "provider": "meta",
            "external_account_id": "123", "meta_page_id": "424242",
            "meta_access_token": "tok-WHAAAAAAAAAAAAAAAAAAAAAA",
            "meta_app_secret": "wh-secret",
            "webhook_verify_token": "verify-me",
            "state": "connected"})
        cls.google_account = cls.env["ads.account"].create({
            "name": "Google WH", "provider": "google",
            "external_account_id": "555", "google_webhook_key": "g-key",
            "state": "connected"})
        cls.form = cls.env["ads.lead.form"]._upsert_from_row(
            cls.meta_account, _form_row(ext="f1"))
        cls.gform = cls.env["ads.lead.form"]._upsert_from_row(
            cls.google_account, _form_row(ext="gf1", name="G Form"))

    def _meta_post(self, payload, secret="wh-secret", tamper=False):
        raw = json.dumps(payload).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if tamper:
            raw = raw + b" "
        return self.url_open("/ads/meta/webhook", data=raw, headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        })

    @staticmethod
    def _leadgen_payload(leadgen_id="L100", page_id="424242"):
        return {"object": "page", "entry": [{
            "id": page_id, "time": 1754700000,
            "changes": [{"field": "leadgen", "value": {
                "leadgen_id": leadgen_id, "page_id": page_id, "form_id": "f1",
                "campaign_id": "c1", "adgroup_id": "s1", "ad_id": "a1",
                "created_time": 1754700000}}]}]}

    # ----------------------------------------------------------------- verify
    def test_meta_handshake(self):
        response = self.url_open(
            "/ads/meta/webhook?hub.mode=subscribe&hub.verify_token=verify-me"
            "&hub.challenge=12345")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "12345")
        response = self.url_open(
            "/ads/meta/webhook?hub.mode=subscribe&hub.verify_token=WRONG"
            "&hub.challenge=12345")
        self.assertEqual(response.status_code, 403)

    # ---------------------------------------------------------------- receive
    def test_meta_delivery_creates_stub(self):
        response = self._meta_post(self._leadgen_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "EVENT_RECEIVED")
        stub = self.env["ads.lead"].search([("external_id", "=", "L100")])
        self.assertEqual(stub.state, "fetch_pending")
        self.assertEqual(stub.form_id, self.form)
        self.assertEqual((stub.platform_refs or {}).get("campaign"), "c1")
        log = self.env["ads.sync.log"].search([("job_type", "=", "webhook")],
                                              order="id desc", limit=1)
        self.assertEqual(log.state, "done")
        # duplicate delivery -> still one staging row
        self._meta_post(self._leadgen_payload())
        self.assertEqual(self.env["ads.lead"].search_count(
            [("external_id", "=", "L100")]), 1)

    def test_meta_tampered_body_rejected(self):
        response = self._meta_post(self._leadgen_payload("L101"), tamper=True)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.env["ads.lead"].search(
            [("external_id", "=", "L101")]))

    def test_meta_wrong_secret_rejected(self):
        response = self._meta_post(self._leadgen_payload("L102"), secret="evil")
        self.assertEqual(response.status_code, 403)

    def test_meta_unknown_page_rejected(self):
        response = self._meta_post(self._leadgen_payload("L103", page_id="999"))
        self.assertEqual(response.status_code, 403)

    def test_meta_malformed_json(self):
        response = self.url_open("/ads/meta/webhook", data=b"{not json",
                                 headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------- stub drain
    def test_stub_drained_by_cron(self):
        self._meta_post(self._leadgen_payload("L110"))
        FakeLeadApi.form_rows = []
        FakeLeadApi.lead_rows = []
        engine = self.env["ads.sync.engine"]

        class StubApi(FakeLeadApi):
            def get_lead(self, lead_external_id):
                return _lead_row(lead_external_id, form_ext="f1",
                                 email="hooked@example.com")

        with patch.dict(account_module.PROVIDER_API,
                        {"meta": StubApi, "google": StubApi}):
            engine._cron_sync_leads()
        lead = self.env["ads.lead"].search([
            ("external_id", "=", "L110"),
            ("account_id", "=", self.meta_account.id)])
        self.assertIn(lead.state, ("processed", "merged"))
        self.assertEqual(lead.email, "hooked@example.com")
        self.assertTrue(lead.crm_lead_id)

    # ------------------------------------------------------------------- GDPR
    def test_data_deletion_endpoint(self):
        import base64
        payload = base64.urlsafe_b64encode(
            json.dumps({"user_id": "U77", "algorithm": "HMAC-SHA256"}).encode()
        ).decode().rstrip("=")
        signature = base64.urlsafe_b64encode(hmac.new(
            b"wh-secret", payload.encode(), hashlib.sha256).digest()).decode()
        response = self.url_open("/ads/meta/data_deletion",
                                 data={"signed_request": f"{signature}.{payload}"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("confirmation_code", body)
        # bad signature -> 403
        response = self.url_open("/ads/meta/data_deletion",
                                 data={"signed_request": f"AAAA.{payload}"})
        self.assertEqual(response.status_code, 403)

    # ------------------------------------------------------------ google hook
    def test_google_lead_webhook(self):
        payload = {
            "google_key": "g-key", "lead_id": "GL1", "form_id": "gf1",
            "campaign_id": 900001, "adgroup_id": 900002, "creative_id": 900003,
            "gcl_id": "CLICK123", "is_test": False,
            "user_column_data": [
                {"column_id": "FULL_NAME", "string_value": "Test Person"},
                {"column_id": "EMAIL", "string_value": "gwh@example.com"},
                {"column_id": "PHONE_NUMBER", "string_value": "+970590000001"},
            ]}
        response = self.url_open("/ads/google/lead_webhook",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 200)
        lead = self.env["ads.lead"].search([("external_id", "=", "GL1")])
        self.assertEqual(lead.state, "new")
        self.assertEqual(lead.email, "gwh@example.com")
        self.assertEqual(lead.gclid, "CLICK123")
        self.assertEqual(lead.form_id, self.gform)
        # replay -> one row
        self.url_open("/ads/google/lead_webhook",
                      data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
        self.assertEqual(self.env["ads.lead"].search_count(
            [("external_id", "=", "GL1")]), 1)
        # wrong key -> 403
        payload["google_key"] = "bad"
        response = self.url_open("/ads/google/lead_webhook",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 403)
