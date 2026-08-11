# -*- coding: utf-8 -*-
from urllib.parse import urlencode

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/auth"
ADWORDS_SCOPE = "https://www.googleapis.com/auth/adwords"


class GoogleAuthWizard(models.TransientModel):
    _name = "ads.google.auth.wizard"
    _description = "Connect Google Ads (one-time OAuth consent)"

    account_id = fields.Many2one(
        "ads.account", required=True, ondelete="cascade",
        default=lambda self: self.env.context.get("active_id"))
    redirect_uri = fields.Char(compute="_compute_redirect_uri")

    @api.depends("account_id")
    def _compute_redirect_uri(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        for wizard in self:
            wizard.redirect_uri = f"{base}/ads/google/oauth_callback"

    def action_connect(self):
        self.ensure_one()
        account = self.account_id.sudo()
        if account.provider != "google":
            raise UserError(_("This wizard only applies to Google Ads accounts."))
        if not (account.google_client_id and account.google_client_secret):
            raise UserError(_(
                "Fill in the OAuth Client ID and Client Secret on the account first "
                "(Google Cloud Console → Credentials)."))
        signature = tools.hmac(self.env(su=True), "ac_ads_connector-google-oauth", account.id)
        params = {
            "client_id": account.google_client_id,
            "redirect_uri": self.redirect_uri,
            "scope": ADWORDS_SCOPE,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "state": f"{account.id}:{signature}",
        }
        return {
            "type": "ir.actions.act_url",
            "url": f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}",
            "target": "self",
        }
