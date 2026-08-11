# -*- coding: utf-8 -*-
import logging
import re
import secrets
from datetime import timedelta

from odoo import _, api, fields, models

from ..tools import PROVIDER_API
from ..tools.ads_api import AdsApiError

_logger = logging.getLogger(__name__)


class AdsAccount(models.Model):
    _name = "ads.account"
    _description = "Ad Platform Account"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sequence, id"

    # ------------------------------------------------------------- identity
    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company)
    provider = fields.Selection(
        [("meta", "Meta (Facebook) Ads"), ("google", "Google Ads")],
        required=True, default="meta", tracking=True)
    state = fields.Selection(
        [("draft", "Not Connected"), ("connected", "Connected"), ("error", "Error")],
        default="draft", copy=False, tracking=True)
    external_account_id = fields.Char(
        string="Platform Account ID",
        help="Meta: the Ad Account number (with or without the act_ prefix). "
             "Google: the Customer ID (digits, dashes allowed).")
    platform_account_name = fields.Char(readonly=True, copy=False)
    currency_id = fields.Many2one("res.currency", readonly=True, copy=False,
                                  string="Account Currency")
    tz_name = fields.Char(string="Account Timezone", readonly=True, copy=False)
    debug_logging = fields.Boolean(
        help="Log every API request/response summary to ir.logging (secrets redacted).")

    # ------------------------------------------------------ meta credentials
    _ADMIN = "ac_ads_connector.group_ads_admin"
    meta_access_token = fields.Char(
        string="System User Token", groups=_ADMIN, copy=False)
    meta_page_access_token = fields.Char(
        string="Page Access Token", groups=_ADMIN, copy=False,
        help="Optional. Lead retrieval is rate-limited per Page; a Page token keeps "
             "lead pulls off the system user's budget.")
    meta_app_secret = fields.Char(string="App Secret", groups=_ADMIN, copy=False)
    meta_business_id = fields.Char(string="Business ID", groups=_ADMIN, copy=False)
    meta_page_id = fields.Char(
        string="Page ID", groups=_ADMIN, copy=False,
        help="The Facebook Page that owns the lead forms (webhook routing key).")
    meta_api_version = fields.Char(default="v26.0", groups=_ADMIN)

    # ---------------------------------------------------- google credentials
    google_auth_mode = fields.Selection(
        [("oauth_refresh", "OAuth2 Refresh Token"), ("service_account", "Service Account")],
        default="oauth_refresh", groups=_ADMIN)
    google_client_id = fields.Char(groups=_ADMIN, copy=False)
    google_client_secret = fields.Char(groups=_ADMIN, copy=False)
    google_refresh_token = fields.Char(groups=_ADMIN, copy=False)
    google_sa_json = fields.Text(
        string="Service Account JSON Key", groups=_ADMIN, copy=False,
        help="Paste the full JSON key. Add the service-account email as a user of the "
             "Google Ads account in the Ads UI (no impersonation needed).")
    google_developer_token = fields.Char(groups=_ADMIN, copy=False)
    google_login_customer_id = fields.Char(
        string="Login Customer ID (MCC)", groups=_ADMIN, copy=False,
        help="Set when access goes through a manager account: the MCC customer id.")
    google_api_version = fields.Char(default="v25", groups=_ADMIN)

    # ------------------------------------------------------- webhooks / misc
    webhook_verify_token = fields.Char(
        groups=_ADMIN, copy=False,
        default=lambda self: secrets.token_urlsafe(24))
    google_webhook_key = fields.Char(
        groups=_ADMIN, copy=False,
        default=lambda self: secrets.token_urlsafe(24),
        help="Key expected from the Google Ads UI lead-form webhook (google_key).")
    callback_url = fields.Char(compute="_compute_callback_url")
    cooldown_until = fields.Datetime(readonly=True, copy=False)
    cooldown_reason = fields.Char(readonly=True, copy=False)
    rate_limit_state = fields.Json(readonly=True, copy=False)

    # ---------------------------------------------------------- lead policy
    lead_sync_enabled = fields.Boolean(default=True)
    lead_auto_convert = fields.Boolean(
        default=True, help="Automatically turn staged platform leads into CRM leads.")
    lead_dedup_mode = fields.Selection(
        [("none", "Never match"), ("email", "By Email"), ("email_phone", "By Email or Phone")],
        default="email_phone", required=True)
    duplicate_policy = fields.Selection(
        [("skip", "Skip duplicate"), ("create_anyway", "Create anyway"),
         ("log_on_existing", "Log on the existing lead")],
        default="log_on_existing", required=True)
    lead_team_id = fields.Many2one("crm.team", string="Default Sales Team")
    lead_user_id = fields.Many2one("res.users", string="Default Salesperson")
    lead_medium_id = fields.Many2one("utm.medium", copy=False, readonly=True)

    use_tracked_urls = fields.Boolean(
        string="Use Tracked Short Links",
        help="Odoo-authored ads land on a click-counting /r/ short link instead "
             "of the raw URL. Note: some platforms review redirect URLs more "
             "strictly.")

    # ---------------------------------------------------- conversion upload
    conversion_upload_enabled = fields.Boolean(
        help="Google only: won CRM leads with a gclid are uploaded back as "
             "click conversions so bidding learns from real revenue.")
    conversion_action_resource = fields.Char(
        groups=_ADMIN, copy=False,
        help="Google conversion action resource name "
             "(customers/<cid>/conversionActions/<id>) of type UPLOAD_CLICKS.")

    # ------------------------------------------------- sync policy/watermarks
    insight_sync_level = fields.Selection(
        [("campaign", "Campaigns only"), ("adset", "+ Ad Sets"), ("ad", "+ Ads")],
        default="ad", required=True,
        help="Depth of daily insight rows. Heavy accounts can sync campaigns only.")
    insight_retention_days = fields.Integer(
        default=400, help="Daily insight rows older than this are compacted to monthly rows.")
    insight_backfill_days = fields.Integer(default=90)
    struct_synced_at = fields.Datetime(readonly=True, copy=False)
    struct_cursor = fields.Json(readonly=True, copy=False)
    insights_synced_until = fields.Date(readonly=True, copy=False)
    insight_backfill_date = fields.Date(readonly=True, copy=False)
    forms_synced_at = fields.Datetime(readonly=True, copy=False)

    _external_account_unique = models.UniqueIndex(
        "(provider, external_account_id, company_id) WHERE external_account_id IS NOT NULL")

    # -------------------------------------------------------------- sanitize
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._sanitize_vals(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._sanitize_vals(vals)
        return super().write(vals)

    @staticmethod
    def _sanitize_vals(vals):
        ext = vals.get("external_account_id")
        if ext:
            ext = ext.strip()
            if ext.startswith("act_"):
                ext = ext[4:]
            vals["external_account_id"] = re.sub(r"[\s\-]", "", ext)

    # -------------------------------------------------------------- computes
    @api.depends("provider")
    def _compute_callback_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        for account in self:
            path = "/ads/meta/webhook" if account.provider == "meta" else "/ads/google/lead_webhook"
            account.callback_url = f"{base}{path}"

    # ------------------------------------------------------------------- api
    def _get_api(self):
        self.ensure_one()
        return PROVIDER_API[self.provider](self)

    def _log_api(self, kind, message):
        """Persist an API trace line. Logging must never break a sync."""
        try:
            self.env["ir.logging"].sudo().create({
                "name": f"ac_ads_connector.{self.provider}",
                "type": "server",
                "level": "DEBUG" if kind in ("request", "response") else "ERROR",
                "dbname": self.env.cr.dbname,
                "message": f"[{self.name}] {kind}: {message}",
                "func": kind,
                "path": "ads.account",
                "line": "0",
            })
        except Exception:  # noqa: BLE001 - observational only
            _logger.exception("ads.account._log_api failed")

    # -------------------------------------------------------------- cooldown
    def _in_cooldown(self):
        """Remaining cooldown in whole seconds (0 when free to call)."""
        self.ensure_one()
        if not self.cooldown_until:
            return 0
        remaining = (self.cooldown_until - fields.Datetime.now()).total_seconds()
        return max(0, int(remaining))

    def _set_cooldown(self, seconds, reason):
        self.ensure_one()
        until = fields.Datetime.now() + timedelta(seconds=seconds)
        current = self.sudo()
        if not current.cooldown_until or current.cooldown_until < until:
            current.write({
                "cooldown_until": until,
                "cooldown_reason": (reason or "")[:500],
            })
        _logger.warning("ads.account %s cooling down %ss: %s", self.name, seconds, reason)

    # --------------------------------------------------------------- actions
    def action_test_connection(self):
        self.ensure_one()
        try:
            info = self._get_api().get_account_info()
        except AdsApiError as exc:
            # No raise: a UserError would roll back the state write with it.
            self.write({"state": "error"})
            return self._notify(_("Connection failed: %s", str(exc)), kind="danger")
        vals = {
            "state": "connected",
            "platform_account_name": info.get("name"),
            "tz_name": info.get("timezone"),
            "cooldown_until": False,
            "cooldown_reason": False,
        }
        currency_code = info.get("currency")
        if currency_code:
            currency = self.env["res.currency"].with_context(active_test=False).search(
                [("name", "=", currency_code)], limit=1)
            if currency:
                vals["currency_id"] = currency.id
        self.write(vals)
        return self._notify(_("Connected to %s (%s).",
                              info.get("name") or self.external_account_id,
                              currency_code or "?"))

    def _handle_auth_failure(self, exc):
        """Token dead / permission revoked: park the account and wake the admins."""
        self.ensure_one()
        self.sudo().write({"state": "error"})
        body = _("Ad account %(name)s can no longer authenticate: %(error)s",
                 name=self.name, error=str(exc))
        self.message_post(body=body)
        try:
            self.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Ad account credentials need attention"),
                note=body,
                user_id=(self.lead_user_id or self.env.ref("base.user_admin")).id,
            )
        except Exception:  # noqa: BLE001 - an alert must never kill the cron
            _logger.exception("could not schedule auth-failure activity")

    @api.model
    def _cron_token_health(self):
        accounts = self.search([("state", "=", "connected")])
        for account in accounts:
            try:
                account._get_api().get_account_info()
            except AdsApiError as exc:
                if exc.failure_type in ("auth", "permission", "account"):
                    account._handle_auth_failure(exc)
                else:
                    _logger.info("token health check on %s: transient %s",
                                 account.name, exc.failure_type)
            except Exception:  # noqa: BLE001 - one account must not block the rest
                _logger.exception("token health check failed for %s", account.name)
            account._check_lead_sync_health()

    def _check_lead_sync_health(self):
        """Google keeps lead data ~30 days only: a lead sync silently dead for
        a day deserves a human, not a log line."""
        self.ensure_one()
        if not self.lead_sync_enabled or self.state != "connected":
            return
        last_ok = self.env["ads.sync.log"].sudo().search([
            ("account_id", "=", self.id),
            ("job_type", "=", "leads"),
            ("state", "in", ("done", "partial")),
        ], order="id desc", limit=1)
        threshold = fields.Datetime.now() - timedelta(hours=24)
        if last_ok and last_ok.started_at > threshold:
            return
        if not last_ok and self.create_date > threshold:
            return  # freshly connected account, first sync still pending
        try:
            self.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Lead sync has not succeeded in 24h"),
                note=_("Account %(name)s: the last successful lead sync is older "
                       "than 24 hours. Platform lead retention is limited — "
                       "investigate before submissions expire.", name=self.name),
                user_id=(self.lead_user_id or self.env.ref("base.user_admin")).id,
            )
        except Exception:  # noqa: BLE001 - an alert must never kill the cron
            _logger.exception("could not schedule lead-staleness activity")

    # ------------------------------------------------------------------- utm
    def _get_utm_source(self):
        self.ensure_one()
        if self.provider == "meta":
            return self.env.ref("utm.utm_source_facebook", raise_if_not_found=False) \
                or self.env.ref("ac_ads_connector.utm_source_google_ads")
        return self.env.ref("ac_ads_connector.utm_source_google_ads")

    def _get_utm_medium(self):
        """Per-account medium, lazily created (enterprise social.account precedent)."""
        self.ensure_one()
        if not self.lead_medium_id:
            label = dict(self._fields["provider"].selection).get(self.provider)
            self.sudo().lead_medium_id = self.env["utm.medium"].sudo().create({
                "name": f"[{label}] {self.name}",
            })
        return self.lead_medium_id

    def action_sync_structure(self):
        self.ensure_one()
        return self.env["ads.sync.engine"]._sync_now(self)

    def action_sync_insights(self):
        self.ensure_one()
        return self.env["ads.sync.engine"]._sync_insights_now(self)

    def action_sync_leads(self):
        self.ensure_one()
        return self.env["ads.sync.engine"]._sync_leads_now(self)

    def action_list_conversion_actions(self):
        """Helper: show the UPLOAD_CLICKS conversion actions so the admin can
        paste the right resource name."""
        self.ensure_one()
        try:
            actions = self._get_api().list_conversion_actions()
        except AdsApiError as exc:
            return self._notify(str(exc), kind="danger")
        if not actions:
            return self._notify(
                _("No UPLOAD_CLICKS conversion action found — create one in "
                  "Google Ads (Goals → Conversions, source 'Import') first."),
                kind="warning")
        listing = "\n".join(f"{a['name']}: {a['resource_name']}" for a in actions)
        self.message_post(body=_("Available conversion actions:\n%s", listing))
        return self._notify(_("%s conversion action(s) listed in the chatter.",
                              len(actions)))

    def action_open_google_auth_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Connect Google Ads"),
            "res_model": "ads.google.auth.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"active_id": self.id},
        }

    # ---------------------------------------------------------------- notify
    def _notify(self, message, kind="success"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": kind, "message": message, "next": {
                "type": "ir.actions.act_window_close"}},
        }
