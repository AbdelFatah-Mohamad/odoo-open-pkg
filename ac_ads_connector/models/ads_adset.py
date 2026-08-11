# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ads_campaign import SYNC_STATUSES


class AdsAdset(models.Model):
    """Meta Ad Set / Google Ad Group — one unified model, labelled per provider
    via `type_name`."""

    _name = "ads.adset"
    _description = "Ad Set / Ad Group"
    _inherit = ["ads.sync.mixin"]
    _order = "id desc"

    _ADS_PUSHABLE_FIELDS = frozenset({
        "name", "status", "budget_type", "budget_amount",
        "optimization_goal", "cpc_bid"})

    name = fields.Char(required=True)
    campaign_id = fields.Many2one(
        "ads.campaign", required=True, ondelete="cascade", index=True)
    account_id = fields.Many2one(
        related="campaign_id.account_id", store=True, index=True)
    provider = fields.Selection(related="account_id.provider", store=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    currency_id = fields.Many2one(related="account_id.currency_id", store=True)

    status = fields.Selection(SYNC_STATUSES, default="paused")
    platform_status = fields.Char(readonly=True)
    type_name = fields.Char(compute="_compute_type_name")

    # Meta
    budget_type = fields.Selection(
        [("daily", "Daily"), ("lifetime", "Lifetime"), ("none", "At Campaign level")],
        default="none")
    budget_amount = fields.Monetary(currency_field="currency_id")
    optimization_goal = fields.Char()
    billing_event = fields.Char()
    targeting = fields.Json()
    promoted_object = fields.Json()
    # Google
    adgroup_type = fields.Char(string="Ad Group Type")
    cpc_bid = fields.Monetary(currency_field="currency_id", string="CPC Bid")

    start_datetime = fields.Datetime()
    stop_datetime = fields.Datetime()
    ad_ids = fields.One2many("ads.ad", "adset_id")

    _external_unique = models.UniqueIndex(
        "(external_id, account_id) WHERE external_id IS NOT NULL")

    @api.depends("provider")
    def _compute_type_name(self):
        for adset in self:
            adset.type_name = "Ad Group" if adset.provider == "google" else "Ad Set"

    # ------------------------------------------------------------ draft push
    def _validate_for_push(self):
        self.ensure_one()
        if not self.campaign_id.external_id:
            raise UserError(
                _("Push the campaign %s to the platform first.",
                  self.campaign_id.display_name))
        missing = []
        if not self.name:
            missing.append(_("a name"))
        if self.provider == "meta":
            if not self.optimization_goal:
                missing.append(_("an optimization goal"))
            if not self.billing_event:
                missing.append(_("a billing event"))
            if not self.targeting:
                missing.append(_("a targeting spec"))
        if missing:
            raise UserError(_("Cannot create the ad set yet, it still needs: %s",
                              ", ".join(missing)))

    def _create_push_payload(self):
        self.ensure_one()
        values = self._json_safe({
            "name": self.name,
            "campaign_external_id": self.campaign_id.external_id,
            "campaign_resource_name": (self.campaign_id.provider_data or {}).get(
                "resource_name"),
            "optimization_goal": self.optimization_goal,
            "billing_event": self.billing_event,
            "targeting": self.targeting,
            "budget_type": self.budget_type,
            "budget_amount": self.budget_amount,
            "cpc_bid": self.cpc_bid,
            "start_datetime": self.start_datetime,
            "stop_datetime": self.stop_datetime,
        })
        return {"object_type": "adset", "values": values}

    @api.model
    def _sync_field_names(self):
        return ("name", "status", "platform_status", "budget_type", "budget_amount",
                "optimization_goal", "billing_event", "targeting", "promoted_object",
                "adgroup_type", "cpc_bid", "start_datetime", "stop_datetime")

    @api.model
    def _prepare_sync_vals(self, account, row):
        vals = {key: row[key] for key in self._sync_field_names() if key in row}
        vals.update({
            "external_id": row["external_id"],
            "sync_status": "synced",
            "raw_payload": row.get("raw"),
            "provider_data": row.get("provider_data"),
            "remote_updated_at": row.get("remote_updated_at"),
            "last_synced_at": fields.Datetime.now(),
        })
        return vals
