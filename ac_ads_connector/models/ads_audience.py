# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AdsAudience(models.Model):
    """Custom audiences / Google user lists. Member uploads carry partner IDS
    in the job payload — normalization + SHA-256 hashing happens at push time
    only and hashed values are never persisted anywhere."""

    _name = "ads.audience"
    _description = "Ad Audience"
    _inherit = ["ads.sync.mixin"]
    _order = "id desc"

    _ADS_PUSHABLE_FIELDS = frozenset({"name", "description"})

    name = fields.Char(required=True)
    account_id = fields.Many2one(
        "ads.account", required=True, ondelete="cascade", index=True)
    provider = fields.Selection(related="account_id.provider", store=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    audience_type = fields.Selection([
        ("custom", "Custom / Customer List"),
        ("lookalike", "Lookalike / Similar"),
        ("website", "Website / Rule-based"),
        ("saved", "Saved / Combined"),
        ("user_list", "Google User List"),
    ], default="custom", required=True)
    description = fields.Text()
    approximate_count = fields.Integer(readonly=True)
    platform_status = fields.Char(readonly=True)
    rule = fields.Json(readonly=True)
    retention_days = fields.Integer(readonly=True)

    _external_unique = models.UniqueIndex(
        "(external_id, account_id) WHERE external_id IS NOT NULL")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("external_id"):
                vals.setdefault("sync_status", "draft")
        return super().create(vals_list)

    # -------------------------------------------------------------------- sync
    @api.model
    def _sync_field_names(self):
        return ("name", "description", "audience_type", "approximate_count",
                "platform_status", "rule", "retention_days")

    @api.model
    def _prepare_sync_vals(self, account, row):
        vals = {key: row[key] for key in self._sync_field_names() if key in row}
        vals.update({
            "external_id": row["external_id"],
            "account_id": account.id,
            "sync_status": "synced",
            "raw_payload": row.get("raw"),
            "provider_data": row.get("provider_data"),
            "remote_updated_at": row.get("remote_updated_at"),
            "last_synced_at": fields.Datetime.now(),
        })
        return vals

    # ------------------------------------------------------------- draft push
    def _push_object_type(self):
        return "audience"

    def _validate_for_push(self):
        self.ensure_one()
        if self.audience_type not in ("custom", "user_list"):
            raise UserError(_(
                "Only customer-list audiences can be created from Odoo "
                "(lookalike/website audiences are built in the platform UI)."))

    def _create_push_payload(self):
        self.ensure_one()
        return {"object_type": "audience",
                "values": {"name": self.name, "description": self.description or ""}}

    # ----------------------------------------------------------------- upload
    def action_open_upload_wizard(self):
        self.ensure_one()
        if not self.external_id:
            raise UserError(_("Create the audience on the platform first."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Upload Audience Members"),
            "res_model": "ads.audience.upload.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_audience_id": self.id},
        }
