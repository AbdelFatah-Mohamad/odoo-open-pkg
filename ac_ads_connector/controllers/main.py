# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac as hmac_lib
import json
import logging

import requests
import werkzeug.exceptions

from odoo import http, tools
from odoo.http import request
from odoo.tools import consteq

_logger = logging.getLogger(__name__)

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
TIMEOUT = (10, 30)


class AdsConnectorController(http.Controller):

    @http.route("/ads/google/oauth_callback", type="http", auth="user", methods=["GET"])
    def google_oauth_callback(self, code=None, state=None, error=None, **kwargs):
        """Landing of the one-time Google OAuth consent started by the wizard.
        Exchanges the code for a refresh token and stores it on the account."""
        if not request.env.user.has_group("ac_ads_connector.group_ads_admin"):
            raise werkzeug.exceptions.Forbidden()
        if error:
            return self._oauth_result_page(f"Google refused the consent: {error}", ok=False)
        if not code or not state or ":" not in state:
            return self._oauth_result_page("Missing code or state.", ok=False)
        account_id, _sep, signature = state.partition(":")
        try:
            account_id = int(account_id)
        except ValueError:
            return self._oauth_result_page("Malformed state.", ok=False)
        expected = tools.hmac(request.env(su=True), "ac_ads_connector-google-oauth", account_id)
        if not consteq(signature, expected):
            _logger.warning("google oauth callback with bad state signature")
            return self._oauth_result_page("Invalid state signature.", ok=False)
        account = request.env["ads.account"].browse(account_id).sudo()
        if not account.exists() or account.provider != "google":
            return self._oauth_result_page("Unknown account.", ok=False)
        base = request.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        try:
            response = requests.post(GOOGLE_TOKEN_ENDPOINT, data={
                "code": code,
                "client_id": account.google_client_id,
                "client_secret": account.google_client_secret,
                "redirect_uri": f"{base}/ads/google/oauth_callback",
                "grant_type": "authorization_code",
            }, timeout=TIMEOUT)
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            _logger.warning("google token exchange failed: %s", exc)
            return self._oauth_result_page("Could not reach Google's token endpoint.", ok=False)
        refresh_token = payload.get("refresh_token")
        if not refresh_token:
            detail = payload.get("error_description") or payload.get("error") or "no refresh token"
            return self._oauth_result_page(
                f"Google did not return a refresh token ({detail}). Re-run the wizard — "
                "the consent screen must show the 'offline access' prompt.", ok=False)
        account.write({"google_refresh_token": refresh_token})
        return self._oauth_result_page(
            "Google Ads connected. You can close this tab, return to Odoo and click "
            "Test Connection.", ok=True)

    # ------------------------------------------------------- meta lead webhook
    @http.route("/ads/meta/webhook", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def meta_webhook_verify(self, **kwargs):
        """Meta subscription handshake: echo hub.challenge when the verify
        token matches ANY meta account's token (constant-time compare)."""
        if kwargs.get("hub.mode") != "subscribe":
            raise werkzeug.exceptions.Forbidden()
        token = kwargs.get("hub.verify_token") or ""
        accounts = request.env["ads.account"].sudo().search(
            [("provider", "=", "meta")])
        for account in accounts:
            if account.webhook_verify_token and consteq(
                    token, account.webhook_verify_token):
                return request.make_response(
                    kwargs.get("hub.challenge") or "",
                    headers=[("Content-Type", "text/plain")])
        _logger.warning("meta webhook verify failed: unknown token")
        raise werkzeug.exceptions.Forbidden()

    @http.route("/ads/meta/webhook", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def meta_webhook_receive(self, **kwargs):
        raw_body = request.httprequest.data or b""
        try:
            payload = json.loads(raw_body)
        except ValueError:
            raise werkzeug.exceptions.BadRequest()
        page_ids = {str(entry.get("id") or "")
                    for entry in payload.get("entry") or []}
        account = self._match_meta_account(page_ids)
        if not account or not self._verify_meta_signature(account, raw_body):
            # strict matching, no fallback: unsigned/unknown deliveries are
            # rejected AND logged, never partially processed
            _logger.warning("meta webhook rejected (pages=%s)", page_ids)
            raise werkzeug.exceptions.Forbidden()
        job = request.env["ads.sync.log"].sudo()._job_start(
            account, "webhook", "webhook")
        created = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field") != "leadgen":
                    continue
                created += self._stub_meta_lead(account, change.get("value") or {})
        job._job_finish("done", records_created=created,
                        payload_sample={"entries": len(payload.get("entry") or [])})
        try:
            request.env.ref("ac_ads_connector.ir_cron_ads_sync_leads")._trigger()
        except Exception:  # noqa: BLE001 - ack must go out regardless
            _logger.exception("could not trigger lead cron from webhook")
        return request.make_response("EVENT_RECEIVED",
                                     headers=[("Content-Type", "text/plain")])

    @staticmethod
    def _match_meta_account(page_ids):
        accounts = request.env["ads.account"].sudo().search(
            [("provider", "=", "meta")])
        for account in accounts:
            if account.meta_page_id and account.meta_page_id in page_ids:
                return account
        return None

    @staticmethod
    def _verify_meta_signature(account, raw_body):
        secret = account.meta_app_secret
        if not secret:
            return False
        header = request.httprequest.headers.get("X-Hub-Signature-256") or ""
        if not header.startswith("sha256="):
            return False
        expected = hmac_lib.new(secret.encode(), raw_body,
                                hashlib.sha256).hexdigest()
        return consteq(header[7:], expected)

    @staticmethod
    def _stub_meta_lead(account, value):
        """Store a fetch_pending stub only — the Graph follow-up fetch happens
        in the cron, never in the public request path."""
        leadgen_id = str(value.get("leadgen_id") or "")
        if not leadgen_id:
            return 0
        form = request.env["ads.lead.form"].sudo().search([
            ("account_id", "=", account.id),
            ("external_id", "=", str(value.get("form_id") or ""))], limit=1)
        row = {
            "external_id": leadgen_id,
            "created_time": None,
            "campaign_external_id": str(value.get("campaign_id") or "") or None,
            "adset_external_id": str(value.get("adgroup_id") or "") or None,
            "ad_external_id": str(value.get("ad_id") or "") or None,
            "raw": value,
        }
        created, _record = request.env["ads.lead"].sudo()._upsert_from_row(
            account, form, row)
        return 1 if created else 0

    # --------------------------------------------------------- meta GDPR hook
    @http.route("/ads/meta/data_deletion", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def meta_data_deletion(self, signed_request=None, **kwargs):
        parsed = self._parse_signed_request(signed_request)
        if parsed is None:
            raise werkzeug.exceptions.Forbidden()
        user_id = parsed.get("user_id") or "unknown"
        code = f"acads-{user_id}"
        _logger.info("meta data deletion request for user %s acknowledged", user_id)
        base = request.env["ir.config_parameter"].sudo().get_param("web.base.url")
        return request.make_response(
            json.dumps({"url": f"{base}/ads/meta/deletion_status?code={code}",
                        "confirmation_code": code}),
            headers=[("Content-Type", "application/json")])

    @http.route("/ads/meta/deletion_status", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def meta_deletion_status(self, code=None, **kwargs):
        return request.make_response(
            "Deletion request processed. This connector stores no Facebook "
            "user-profile data; lead submissions are managed inside the CRM.",
            headers=[("Content-Type", "text/plain")])

    @staticmethod
    def _parse_signed_request(signed_request):
        if not signed_request or "." not in signed_request:
            return None

        def b64d(segment):
            segment += "=" * (-len(segment) % 4)
            return base64.urlsafe_b64decode(segment)

        signature_b64, payload_b64 = signed_request.split(".", 1)
        try:
            signature = b64d(signature_b64)
            payload = json.loads(b64d(payload_b64))
        except (ValueError, TypeError):
            return None
        accounts = request.env["ads.account"].sudo().search(
            [("provider", "=", "meta"), ("meta_app_secret", "!=", False)])
        for account in accounts:
            expected = hmac_lib.new(account.meta_app_secret.encode(),
                                    payload_b64.encode(), hashlib.sha256).digest()
            if hmac_lib.compare_digest(signature, expected):
                return payload
        return None

    # ------------------------------------------------------ google fast path
    @http.route("/ads/google/lead_webhook", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def google_lead_webhook(self, **kwargs):
        """Google Ads UI lead-form webhook (google_key auth). Polling stays on
        as the reconciliation net; dedupe on lead_id makes the overlap free."""
        try:
            payload = json.loads(request.httprequest.data or b"")
        except ValueError:
            raise werkzeug.exceptions.BadRequest()
        key = payload.get("google_key") or ""
        account = None
        for candidate in request.env["ads.account"].sudo().search(
                [("provider", "=", "google")]):
            if candidate.google_webhook_key and consteq(
                    key, candidate.google_webhook_key):
                account = candidate
                break
        if not account:
            _logger.warning("google lead webhook rejected: bad key")
            raise werkzeug.exceptions.Forbidden()
        lead_id = str(payload.get("lead_id") or "")
        if not lead_id:
            raise werkzeug.exceptions.BadRequest()
        form = request.env["ads.lead.form"].sudo().search([
            ("account_id", "=", account.id),
            ("external_id", "=", str(payload.get("form_id") or ""))], limit=1)
        entries = []
        for column in payload.get("user_column_data") or []:
            entry_key = (column.get("column_id") or column.get("column_name")
                         or "").lower()
            entries.append({"key": entry_key,
                            "values": [column.get("string_value")]})
        row = {
            "external_id": lead_id,
            "created_time": None,
            "campaign_external_id": str(payload.get("campaign_id") or "") or None,
            "adset_external_id": str(payload.get("adgroup_id") or "") or None,
            "ad_external_id": str(payload.get("creative_id") or "") or None,
            "gclid": payload.get("gcl_id") or None,
            "is_organic": False,
            "fields": entries,
            "raw": payload,
        }
        request.env["ads.lead"].sudo()._upsert_from_row(account, form, row)
        job = request.env["ads.sync.log"].sudo()._job_start(
            account, "webhook", "webhook")
        job._job_finish("done", records_created=1)
        return request.make_response(json.dumps({}),
                                     headers=[("Content-Type", "application/json")])

    @staticmethod
    def _oauth_result_page(message, ok):
        color = "#2e7d32" if ok else "#c62828"
        title = "Success" if ok else "Connection failed"
        return request.make_response(
            f"<html><body style='font-family:sans-serif;max-width:40em;margin:4em auto'>"
            f"<h2 style='color:{color}'>{title}</h2><p>{tools.html_escape(message)}</p>"
            f"</body></html>",
            headers=[("Content-Type", "text/html; charset=utf-8")])
