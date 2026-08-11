# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.http import request

from .ir_http import CLICK_ID_PARAMS

_logger = logging.getLogger(__name__)


class CrmLead(models.Model):
    _inherit = "crm.lead"

    ads_lead_ids = fields.One2many("ads.lead", "crm_lead_id")
    ads_lead_count = fields.Integer(compute="_compute_ads_lead_count")
    # Filled from the odoo_gclid/odoo_fbclid cookies (see models/ir_http.py)
    # on website-form leads, or copied from the ads.lead staging record.
    gclid = fields.Char(string="Google Click ID", index="btree_not_null", copy=False)
    fbclid = fields.Char(string="Facebook Click ID", copy=False)

    @api.model
    def default_get(self, fields_list):
        """Read the click-id cookies onto new leads — same semantics as
        utm.mixin.default_get: skipped for salespeople creating leads by hand
        (their own browsing cookies are not attribution), applied on the
        website-form SUPERUSER path."""
        values = super().default_get(fields_list)
        is_salesman = (not self.env.is_superuser()
                       and self.env.user.has_group("sales_team.group_sale_salesman"))
        if request and not is_salesman:
            field_by_param = {"gclid": "gclid", "fbclid": "fbclid"}
            for url_parameter, cookie_name in CLICK_ID_PARAMS:
                field_name = field_by_param[url_parameter]
                if field_name in fields_list and not values.get(field_name):
                    cookie_value = request.cookies.get(cookie_name)
                    if cookie_value:
                        values[field_name] = cookie_value
        return values

    def write(self, vals):
        result = super().write(vals)
        if vals.get("stage_id"):
            won = self.filtered(lambda lead: lead.stage_id.is_won)
            if won:
                won._ads_enqueue_conversions()
        return result

    def _ads_enqueue_conversions(self):
        """Won lead -> one Google click-conversion job per attributed platform
        submission — and for WEBSITE-originated leads that carry a gclid (from
        the odoo_gclid cookie) without any ads.lead, one job on the lead
        itself. Idempotent: re-winning never duplicates an upload."""
        Job = self.env["ads.push.job"].sudo()
        for lead in self:
            if not lead.ads_lead_ids and lead.gclid:
                lead._ads_enqueue_website_conversion(Job)
                continue
            for ads_lead in lead.ads_lead_ids:
                account = ads_lead.account_id
                if (account.provider != "google"
                        or not account.conversion_upload_enabled
                        or not account.sudo().conversion_action_resource):
                    continue
                already = Job.search_count([
                    ("operation", "=", "conversion_upload"),
                    ("res_model", "=", "ads.lead"),
                    ("res_id", "=", ads_lead.id),
                    ("state", "in", ("queued", "in_progress", "done"))])
                if already:
                    continue
                currency = account.currency_id or lead.company_id.currency_id
                Job._enqueue(ads_lead, "conversion_upload", {
                    "crm_lead_id": lead.id,
                    "gclid": ads_lead.gclid or None,
                    "value": lead.expected_revenue or 0.0,
                    "currency": currency.name or "USD",
                    "conversion_datetime": fields.Datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S+00:00"),
                })
                try:
                    self.env.ref(
                        "ac_ads_connector.ir_cron_ads_push_queue")._trigger()
                except Exception:  # noqa: BLE001 - queue drain is best-effort
                    _logger.exception("could not trigger push queue")

    def _ads_enqueue_website_conversion(self, Job):
        """gclid-carrying lead with no platform staging record: upload through
        the (single) conversion-enabled Google account. Multiple enabled
        accounts cannot be disambiguated from a gclid — first wins, documented."""
        self.ensure_one()
        account = self.env["ads.account"].sudo().search([
            ("provider", "=", "google"),
            ("state", "=", "connected"),
            ("conversion_upload_enabled", "=", True),
            ("conversion_action_resource", "!=", False),
        ], limit=1)
        if not account:
            return
        already = Job.search_count([
            ("operation", "=", "conversion_upload"),
            ("res_model", "=", "crm.lead"),
            ("res_id", "=", self.id),
            ("state", "in", ("queued", "in_progress", "done"))])
        if already:
            return
        currency = account.currency_id or self.company_id.currency_id
        job = Job.create([{
            "account_id": account.id,
            "res_model": "crm.lead",
            "res_id": self.id,
            "operation": "conversion_upload",
            "payload": {
                "crm_lead_id": self.id,
                "gclid": self.gclid,
                "value": self.expected_revenue or 0.0,
                "currency": currency.name or "USD",
                "conversion_datetime": fields.Datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S+00:00"),
            },
        }])
        try:
            self.env.ref("ac_ads_connector.ir_cron_ads_push_queue")._trigger()
        except Exception:  # noqa: BLE001 - queue drain is best-effort
            _logger.exception("could not trigger push queue")
        return job

    @api.depends("ads_lead_ids")
    def _compute_ads_lead_count(self):
        for lead in self:
            lead.ads_lead_count = len(lead.ads_lead_ids)

    def action_view_ads_leads(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Ad Submissions"),
            "res_model": "ads.lead",
            "view_mode": "list,form",
            "domain": [("crm_lead_id", "=", self.id)],
        }
