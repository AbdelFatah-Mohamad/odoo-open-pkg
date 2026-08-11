# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AdsInsight(models.Model):
    """Metric fact rows: one row per (account, level, object, date, granularity).

    Daily rows are UPSERTed — Meta restates attribution for up to 28 days, so
    the same key is legitimately re-written until it freezes. Dailies older
    than the account's retention are compacted into `granularity='month'` rows
    (metrics summed; `reach` too, accepting that summed reach is no longer
    deduplicated across days)."""

    _name = "ads.insight"
    _description = "Ad Insight"
    _order = "date desc, id desc"

    account_id = fields.Many2one(
        "ads.account", required=True, ondelete="cascade", index=True)
    provider = fields.Selection(related="account_id.provider", store=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    currency_id = fields.Many2one(related="account_id.currency_id", store=True)
    level = fields.Selection([
        ("account", "Account"), ("campaign", "Campaign"),
        ("adset", "Ad Set"), ("ad", "Ad"),
    ], required=True, index=True)
    campaign_id = fields.Many2one(
        "ads.campaign", ondelete="cascade", index="btree_not_null")
    adset_id = fields.Many2one(
        "ads.adset", ondelete="cascade", index="btree_not_null")
    ad_id = fields.Many2one("ads.ad", ondelete="cascade", index="btree_not_null")
    utm_campaign_id = fields.Many2one(
        related="campaign_id.utm_campaign_id", store=True, index="btree_not_null")
    date = fields.Date(required=True, index=True)
    granularity = fields.Selection(
        [("day", "Day"), ("month", "Month")], default="day", required=True)
    breakdown_key = fields.Char(default="", required=True)

    spend = fields.Monetary(currency_field="currency_id")
    impressions = fields.Integer(aggregator="sum")
    clicks = fields.Integer(aggregator="sum")
    reach = fields.Integer(
        aggregator="sum",
        help="Meta only. On monthly rows this is a sum of daily reach — no longer "
             "deduplicated across days.")
    conversions = fields.Float(aggregator="sum")
    conversion_value = fields.Monetary(currency_field="currency_id")
    video_views = fields.Integer(aggregator="sum")
    leads_count = fields.Integer(aggregator="sum", string="Leads (platform)")

    ctr = fields.Float(compute="_compute_rates", string="CTR (%)")
    cpc = fields.Float(compute="_compute_rates", string="CPC")
    cpm = fields.Float(compute="_compute_rates", string="CPM")
    cpl = fields.Float(compute="_compute_rates", string="CPL")

    _insight_unique = models.UniqueIndex(
        "(account_id, level, date, granularity, breakdown_key, "
        "COALESCE(campaign_id, 0), COALESCE(adset_id, 0), COALESCE(ad_id, 0))")

    _METRIC_FIELDS = ("spend", "impressions", "clicks", "reach", "conversions",
                      "conversion_value", "video_views", "leads_count")

    @api.depends("spend", "impressions", "clicks", "leads_count")
    def _compute_rates(self):
        for row in self:
            row.ctr = row.impressions and round(100.0 * row.clicks / row.impressions, 2)
            row.cpc = row.clicks and round(row.spend / row.clicks, 4)
            row.cpm = row.impressions and round(1000.0 * row.spend / row.impressions, 4)
            row.cpl = row.leads_count and round(row.spend / row.leads_count, 2)

    # ------------------------------------------------------------------ upsert
    @api.model
    def _upsert_from_rows(self, account, level, rows):
        """Bulk idempotent upsert of normalized insight rows (granularity=day).
        Rows whose mirrored object is missing (structure lag) are skipped —
        the next structure sync + insight re-pull picks them up."""
        counts = dict(records_created=0, records_updated=0,
                      records_skipped=0, records_failed=0)
        if not rows:
            return counts
        maps = self._object_maps(account, rows)
        dates = {row["date"] for row in rows}
        existing = {}
        for record in self.sudo().search([
                ("account_id", "=", account.id), ("level", "=", level),
                ("granularity", "=", "day"), ("date", "in", list(dates))]):
            key = (record.date, record.campaign_id.id, record.adset_id.id,
                   record.ad_id.id, record.breakdown_key or "")
            existing[key] = record
        to_create = []
        for row in rows:
            try:
                links = self._resolve_links(level, row, maps)
                if links is None:
                    counts["records_skipped"] += 1
                    continue
                campaign_id, adset_id, ad_id = links
                key = (row["date"], campaign_id, adset_id, ad_id, "")
                metrics = {name: row.get(name, 0) for name in self._METRIC_FIELDS}
                record = existing.get(key)
                if record:
                    record.write(metrics)
                    counts["records_updated"] += 1
                else:
                    to_create.append(dict(
                        metrics, account_id=account.id, level=level,
                        date=row["date"], granularity="day", breakdown_key="",
                        campaign_id=campaign_id, adset_id=adset_id, ad_id=ad_id))
                    counts["records_created"] += 1
            except Exception:  # noqa: BLE001 - row isolation
                counts["records_failed"] += 1
                _logger.exception("insight upsert failed for %s", row)
        if to_create:
            self.sudo().create(to_create)
        return counts

    @api.model
    def _object_maps(self, account, rows):
        def build(model, key):
            wanted = {row.get(key) for row in rows if row.get(key)}
            if not wanted:
                return {}
            records = self.env[model].sudo().with_context(active_test=False).search([
                ("account_id", "=", account.id),
                ("external_id", "in", list(wanted))])
            return {record.external_id: record.id for record in records}
        return {
            "campaign": build("ads.campaign", "campaign_external_id"),
            "adset": build("ads.adset", "adset_external_id"),
            "ad": build("ads.ad", "ad_external_id"),
        }

    @staticmethod
    def _resolve_links(level, row, maps):
        """Return (campaign_id, adset_id, ad_id) or None when the mirrored
        object for the row's level is missing."""
        campaign_id = maps["campaign"].get(row.get("campaign_external_id")) or False
        adset_id = maps["adset"].get(row.get("adset_external_id")) or False
        ad_id = maps["ad"].get(row.get("ad_external_id")) or False
        if level == "campaign" and not campaign_id:
            return None
        if level == "adset" and not adset_id:
            return None
        if level == "ad" and not ad_id:
            return None
        return campaign_id, adset_id, ad_id

    # -------------------------------------------------------------- compaction
    @api.model
    def _compact_old_daily_rows(self, account):
        """Roll dailies older than the account retention into month rows.
        Sums merge into an existing month row when one is already there."""
        cutoff = fields.Date.today() - timedelta(days=account.insight_retention_days)
        dailies = self.sudo().search([
            ("account_id", "=", account.id),
            ("granularity", "=", "day"),
            ("date", "<", cutoff)])
        if not dailies:
            return 0
        buckets = {}
        for row in dailies:
            month = row.date.replace(day=1)
            key = (row.level, row.campaign_id.id, row.adset_id.id, row.ad_id.id, month)
            bucket = buckets.setdefault(key, {name: 0 for name in self._METRIC_FIELDS})
            for name in self._METRIC_FIELDS:
                bucket[name] += row[name]
        for (level, campaign_id, adset_id, ad_id, month), sums in buckets.items():
            domain = [
                ("account_id", "=", account.id), ("level", "=", level),
                ("granularity", "=", "month"), ("date", "=", month),
                ("campaign_id", "=", campaign_id or False),
                ("adset_id", "=", adset_id or False),
                ("ad_id", "=", ad_id or False)]
            month_row = self.sudo().search(domain, limit=1)
            if month_row:
                month_row.write({name: month_row[name] + sums[name]
                                 for name in self._METRIC_FIELDS})
            else:
                self.sudo().create([dict(
                    sums, account_id=account.id, level=level, granularity="month",
                    breakdown_key="", date=month, campaign_id=campaign_id or False,
                    adset_id=adset_id or False, ad_id=ad_id or False)])
        count = len(dailies)
        dailies.unlink()
        _logger.info("compacted %s daily insight rows for %s", count, account.name)
        return count

    @api.model
    def _cron_compact(self):
        for account in self.env["ads.account"].search([]):
            self._compact_old_daily_rows(account)
