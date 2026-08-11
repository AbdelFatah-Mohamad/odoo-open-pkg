# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AdsCreative(models.Model):
    """Ad creative content. Meta exposes creatives as first-class objects
    (embedded in the ads pull); Google ad content lives on the ad itself, so
    Google ads reference no creative record in the pull phases."""

    _name = "ads.creative"
    _description = "Ad Creative"
    _order = "id desc"

    name = fields.Char()
    account_id = fields.Many2one(
        "ads.account", required=True, ondelete="cascade", index=True)
    provider = fields.Selection(related="account_id.provider", store=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    external_id = fields.Char(index=True, copy=False, readonly=True)

    title = fields.Char()
    body = fields.Text()
    call_to_action = fields.Char()
    image_url = fields.Char()
    thumbnail_url = fields.Char()
    link_url = fields.Char()
    object_story_spec = fields.Json()
    asset_data = fields.Json()
    raw_payload = fields.Json(copy=False)

    _external_unique = models.UniqueIndex(
        "(external_id, account_id) WHERE external_id IS NOT NULL")

    @api.model
    def _upsert_from_row(self, account, row):
        """Idempotent upsert of an embedded creative dict; returns the record."""
        if not row or not row.get("external_id"):
            return self.browse()
        creative = self.search([
            ("account_id", "=", account.id),
            ("external_id", "=", row["external_id"]),
        ], limit=1)
        vals = {
            "name": row.get("name"),
            "title": row.get("title"),
            "body": row.get("body"),
            "call_to_action": row.get("call_to_action"),
            "image_url": row.get("image_url"),
            "thumbnail_url": row.get("thumbnail_url"),
            "link_url": row.get("link_url"),
            "object_story_spec": row.get("object_story_spec"),
            "raw_payload": row.get("raw"),
        }
        if creative:
            creative.write(vals)
        else:
            creative = self.create([dict(vals, account_id=account.id,
                                         external_id=row["external_id"])])
        return creative
