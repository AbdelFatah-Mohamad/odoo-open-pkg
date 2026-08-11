# -*- coding: utf-8 -*-
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: unified status vocabulary shared by campaign/adset/ad (per-provider raw kept
#: in platform_status).
SYNC_STATUSES = [
    ("enabled", "Active"),
    ("paused", "Paused"),
    ("removed", "Removed"),
    ("archived", "Archived"),
]

OBJECTIVES = [
    ("awareness", "Awareness"),
    ("traffic", "Traffic"),
    ("engagement", "Engagement"),
    ("leads", "Leads"),
    ("app", "App Promotion"),
    ("sales", "Sales"),
    ("other", "Other"),
]


class AdsCampaign(models.Model):
    """Mirror of a platform campaign. Delegates to utm.campaign (`_inherits`)
    so every ads campaign IS a utm.campaign: the display name is the delegated
    `title` (utm's `name` stays the auto-unique identifier) and CRM/Sales UTM
    reporting picks these campaigns up natively."""

    _name = "ads.campaign"
    _description = "Ad Campaign"
    _inherit = ["ads.sync.mixin", "mail.thread", "mail.activity.mixin"]
    _inherits = {"utm.campaign": "utm_campaign_id"}
    _rec_name = "title"
    _order = "id desc"

    #: user edits to these flip synced -> local_changes (write-back scope).
    _ADS_PUSHABLE_FIELDS = frozenset({
        "title", "status", "budget_type", "budget_amount",
        "start_datetime", "stop_datetime", "bid_strategy"})

    utm_campaign_id = fields.Many2one(
        "utm.campaign", required=True, ondelete="cascade", index=True, copy=False,
        string="UTM Campaign")
    account_id = fields.Many2one(
        "ads.account", required=True, ondelete="cascade", index=True)
    provider = fields.Selection(related="account_id.provider", store=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    currency_id = fields.Many2one(related="account_id.currency_id", store=True)

    status = fields.Selection(SYNC_STATUSES, default="paused", tracking=True)
    platform_status = fields.Char(readonly=True,
                                  help="Verbatim platform status (incl. effective status).")
    objective = fields.Selection(OBJECTIVES, default="other")
    objective_raw = fields.Char(
        help="Verbatim platform objective / channel type — pushed back unchanged.")
    budget_type = fields.Selection(
        [("daily", "Daily"), ("lifetime", "Lifetime"), ("none", "At Ad Set level")],
        default="daily")
    budget_amount = fields.Monetary(currency_field="currency_id")
    google_budget_resource = fields.Char(copy=False, readonly=True)
    bid_strategy = fields.Char()
    special_ad_categories = fields.Json()
    start_datetime = fields.Datetime()
    stop_datetime = fields.Datetime()

    adset_ids = fields.One2many("ads.adset", "campaign_id")
    ad_ids = fields.One2many("ads.ad", "campaign_id")
    insight_ids = fields.One2many("ads.insight", "campaign_id")
    tracked_click_count = fields.Integer(compute="_compute_tracked_click_count",
                                         string="Tracked Clicks")
    adset_count = fields.Integer(compute="_compute_child_counts")
    ad_count = fields.Integer(compute="_compute_child_counts")

    spend_30d = fields.Monetary(compute="_compute_kpi_30d", currency_field="currency_id",
                                string="Spend (30d)")
    impressions_30d = fields.Integer(compute="_compute_kpi_30d", string="Impressions (30d)")
    clicks_30d = fields.Integer(compute="_compute_kpi_30d", string="Clicks (30d)")
    insight_leads_30d = fields.Integer(compute="_compute_kpi_30d", string="Leads (30d)")
    cpl_30d = fields.Monetary(compute="_compute_kpi_30d", currency_field="currency_id",
                              string="Cost per Lead (30d)")

    _external_unique = models.UniqueIndex(
        "(external_id, account_id) WHERE external_id IS NOT NULL")

    @api.depends("adset_ids", "ad_ids")
    def _compute_child_counts(self):
        for campaign in self:
            campaign.adset_count = len(campaign.adset_ids)
            campaign.ad_count = len(campaign.ad_ids)

    def _compute_kpi_30d(self):
        since = fields.Date.today() - timedelta(days=30)
        grouped = self.env["ads.insight"]._read_group(
            [("campaign_id", "in", self.ids), ("level", "=", "campaign"),
             ("date", ">=", since)],
            ["campaign_id"],
            ["spend:sum", "impressions:sum", "clicks:sum", "leads_count:sum"])
        by_campaign = {campaign.id: (spend, impressions, clicks, leads)
                       for campaign, spend, impressions, clicks, leads in grouped}
        for campaign in self:
            spend, impressions, clicks, leads = by_campaign.get(
                campaign.id, (0.0, 0, 0, 0))
            campaign.spend_30d = spend or 0.0
            campaign.impressions_30d = impressions or 0
            campaign.clicks_30d = clicks or 0
            campaign.insight_leads_30d = leads or 0
            campaign.cpl_30d = round(spend / leads, 2) if leads else 0.0

    # ---------------------------------------------------------------- lifecycle
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # The delegated utm.campaign is created from the leftover vals;
            # mark it auto so it is distinguishable from hand-made campaigns.
            if not vals.get("utm_campaign_id"):
                vals.setdefault("is_auto_campaign", True)
            if not vals.get("external_id"):
                vals.setdefault("sync_status", "draft")
        return super().create(vals_list)

    def unlink(self):
        utm_campaigns = self.utm_campaign_id
        result = super().unlink()
        for utm in utm_campaigns:
            if utm.exists():
                # Keep attribution history alive: archive instead of delete when
                # any lead still points at the campaign.
                if self.env["crm.lead"].with_context(active_test=False).search_count(
                        [("campaign_id", "=", utm.id)], limit=1):
                    utm.active = False
                else:
                    utm.unlink()
        return result

    # -------------------------------------------------------------------- sync
    @api.model
    def _sync_field_names(self):
        """Normalized-row keys applied on pull (title comes from row['name'])."""
        return ("status", "platform_status", "objective", "objective_raw",
                "budget_type", "budget_amount", "google_budget_resource",
                "bid_strategy", "special_ad_categories", "start_datetime",
                "stop_datetime")

    @api.model
    def _prepare_sync_vals(self, account, row):
        vals = {key: row[key] for key in self._sync_field_names() if key in row}
        vals.update({
            "title": row["name"],
            "external_id": row["external_id"],
            "account_id": account.id,
            "sync_status": "synced",
            "raw_payload": row.get("raw"),
            "provider_data": row.get("provider_data"),
            "remote_updated_at": row.get("remote_updated_at"),
            "last_synced_at": fields.Datetime.now(),
        })
        return vals

    # ------------------------------------------------------------ draft push
    #: unified objective -> platform-native objective when objective_raw unset.
    _META_OBJECTIVE_DEFAULT = {
        "awareness": "OUTCOME_AWARENESS", "traffic": "OUTCOME_TRAFFIC",
        "engagement": "OUTCOME_ENGAGEMENT", "leads": "OUTCOME_LEADS",
        "app": "OUTCOME_APP_PROMOTION", "sales": "OUTCOME_SALES"}

    def _resolved_objective_raw(self):
        self.ensure_one()
        if self.objective_raw:
            return self.objective_raw
        if self.provider == "meta":
            return self._META_OBJECTIVE_DEFAULT.get(self.objective)
        return None  # google: the client derives the channel from `objective`

    def _validate_for_push(self):
        self.ensure_one()
        missing = []
        if not self.title:
            missing.append(_("a name"))
        if self.provider == "meta" and not self._resolved_objective_raw():
            missing.append(_("an objective"))
        if self.provider == "google" and not self.budget_amount:
            missing.append(_("a budget amount (Google campaigns require one)"))
        if self.provider == "meta" and self.budget_type != "none" \
                and not self.budget_amount:
            missing.append(_("a budget amount (or budget at Ad Set level)"))
        if missing:
            raise UserError(_("Cannot create the campaign yet, it still needs: %s",
                              ", ".join(missing)))

    def _create_push_payload(self):
        self.ensure_one()
        values = self._json_safe({
            "title": self.title,
            "objective": self.objective,
            "objective_raw": self._resolved_objective_raw(),
            "budget_type": self.budget_type,
            "budget_amount": self.budget_amount,
            "special_ad_categories": self.special_ad_categories or [],
            "bid_strategy": self.bid_strategy,
            "start_datetime": self.start_datetime,
            "stop_datetime": self.stop_datetime,
        })
        return {"object_type": "campaign", "values": values}

    def _compute_tracked_click_count(self):
        for campaign in self:
            campaign.tracked_click_count = sum(
                campaign.ad_ids.mapped("click_count"))

    def action_view_tracked_links(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Tracked Links"),
            "res_model": "link.tracker",
            "view_mode": "list,form",
            "domain": [("id", "in", self.ad_ids.link_tracker_id.ids)],
        }

    # ------------------------------------------------------------ url tagging
    def _utm_tag_url(self, url):
        """Append this campaign's utm_source/utm_medium/utm_campaign to a
        landing URL, using the exact utm record NAMES so the visitor's cookies
        later resolve (via _find_or_create_record's =ilike name match) to the
        SAME records instead of creating near-duplicates. Existing query
        params are preserved; already-present utm params are never overwritten
        (respect deliberate manual tagging)."""
        self.ensure_one()
        if not url:
            return url
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        account = self.account_id
        tags = {
            "utm_source": account._get_utm_source().name,
            "utm_medium": account._get_utm_medium().name,
            "utm_campaign": self.utm_campaign_id.name,
        }
        for key, value in tags.items():
            if value and key not in query:
                query[key] = value
        return urlunsplit(parts._replace(query=urlencode(query)))

    # ----------------------------------------------------------------- actions
    def action_view_adsets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Ad Sets"),
            "res_model": "ads.adset",
            "view_mode": "list,form",
            "domain": [("campaign_id", "=", self.id)],
            "context": {"default_campaign_id": self.id},
        }

    def action_view_ads(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Ads"),
            "res_model": "ads.ad",
            "view_mode": "list,form",
            "domain": [("campaign_id", "=", self.id)],
        }

    def action_open_in_platform(self):
        self.ensure_one()
        if self.provider == "meta":
            url = (f"https://adsmanager.facebook.com/adsmanager/manage/campaigns"
                   f"?act={self.account_id.external_account_id}")
        else:
            url = "https://ads.google.com/aw/campaigns"
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}
