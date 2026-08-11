# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ads_campaign import SYNC_STATUSES


class AdsAd(models.Model):
    _name = "ads.ad"
    _description = "Ad"
    _inherit = ["ads.sync.mixin"]
    _order = "id desc"

    _ADS_PUSHABLE_FIELDS = frozenset({"name", "status"})

    name = fields.Char(required=True)
    adset_id = fields.Many2one(
        "ads.adset", required=True, ondelete="cascade", index=True)
    campaign_id = fields.Many2one(
        related="adset_id.campaign_id", store=True, index=True)
    account_id = fields.Many2one(
        related="adset_id.account_id", store=True, index=True)
    provider = fields.Selection(related="account_id.provider", store=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    currency_id = fields.Many2one(related="account_id.currency_id", store=True)

    status = fields.Selection(SYNC_STATUSES, default="paused")
    platform_status = fields.Char(readonly=True)
    creative_id = fields.Many2one("ads.creative", ondelete="set null")
    preview_url = fields.Char()
    google_ad_type = fields.Char()
    final_urls = fields.Json()
    link_tracker_id = fields.Many2one("link.tracker", copy=False, ondelete="set null")
    click_count = fields.Integer(related="link_tracker_id.count",
                                 string="Tracked Clicks")

    _external_unique = models.UniqueIndex(
        "(external_id, account_id) WHERE external_id IS NOT NULL")

    # ------------------------------------------------------------ link tracker
    def _target_url(self):
        """The ad's landing URL: creative link (Meta) or first final URL."""
        self.ensure_one()
        creative_link = ((self.creative_id.object_story_spec or {})
                         .get("link_data") or {}).get("link")
        final = (self.final_urls or [None])[0] if self.final_urls else None
        return creative_link or final

    def action_create_tracked_link(self):
        """Get-or-create a link.tracker for the ad's landing URL. The short
        /r/ redirect counts clicks and re-appends the tracker's own UTM
        parameters on redirect, so cookie attribution stays intact."""
        for ad in self:
            url = ad._target_url()
            if not url:
                raise UserError(_("Ad %s has no landing URL to track.",
                                  ad.display_name))
            campaign = ad.campaign_id
            tracker = self.env["link.tracker"].search_or_create([{
                "url": url,
                "campaign_id": campaign.utm_campaign_id.id or False,
                "source_id": ad.account_id._get_utm_source().id or False,
                "medium_id": ad.account_id._get_utm_medium().id or False,
                "label": ad.name,
            }])
            ad.with_context(skip_ads_sync=True).link_tracker_id = tracker.id
        return True

    # ------------------------------------------------------------ draft push
    def _validate_for_push(self):
        self.ensure_one()
        if self.provider == "google":
            raise UserError(_(
                "Google ads must be authored in Google Ads itself (their content "
                "is immutable through the API); the hourly sync will mirror them "
                "back here."))
        if not self.adset_id.external_id:
            raise UserError(_("Push the ad set %s to the platform first.",
                              self.adset_id.display_name))
        creative = self.creative_id
        if not (creative and (creative.external_id or creative.object_story_spec)):
            raise UserError(_(
                "The ad needs a creative: link an existing synced creative or one "
                "carrying an object_story_spec."))

    def _create_push_payload(self):
        self.ensure_one()
        creative = self.creative_id
        values = {
            "name": self.name,
            "adset_external_id": self.adset_id.external_id,
            "creative_external_id": creative.external_id or None,
        }
        if not creative.external_id:
            values["creative"] = {
                "name": creative.name,
                "object_story_spec": self._utm_tagged_story_spec(
                    creative.object_story_spec),
            }
        return {"object_type": "ad", "values": values}

    def _utm_tagged_story_spec(self, spec):
        """Auto-tag the landing link of an Odoo-authored creative with the
        campaign's UTM parameters — or, when the account opts into tracked
        URLs, swap it for the click-counting /r/ short link (which re-appends
        the tracker's UTM params on redirect). Platform-authored creatives
        synced back are never rewritten — this only runs on the draft-push path."""
        self.ensure_one()
        if not spec or not self.campaign_id:
            return spec
        spec = dict(spec)
        link_data = dict(spec.get("link_data") or {})
        if link_data.get("link"):
            if self.account_id.use_tracked_urls:
                if not self.link_tracker_id:
                    self.action_create_tracked_link()
                link_data["link"] = self.link_tracker_id.short_url
            else:
                link_data["link"] = self.campaign_id._utm_tag_url(link_data["link"])
            spec["link_data"] = link_data
        return spec

    @api.model
    def _sync_field_names(self):
        return ("name", "status", "platform_status", "preview_url",
                "google_ad_type", "final_urls")

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
        if row.get("creative"):
            creative = self.env["ads.creative"]._upsert_from_row(account, row["creative"])
            if creative:
                vals["creative_id"] = creative.id
        return vals
