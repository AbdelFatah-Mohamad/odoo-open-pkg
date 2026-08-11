# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.tools import drop_view_if_exists


class AdsPerformanceReport(models.Model):
    """Monthly spend (ads.insight, campaign level) ⋈ CRM outcome (crm.lead by
    utm campaign): the ROAS / cost-per-lead view the whole module feeds."""

    _name = "ads.performance.report"
    _description = "Ads Performance (Spend vs CRM)"
    _auto = False
    _order = "month desc"

    utm_campaign_id = fields.Many2one("utm.campaign", readonly=True)
    account_id = fields.Many2one("ads.account", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    month = fields.Date(readonly=True)
    spend = fields.Monetary(currency_field="currency_id", readonly=True,
                            aggregator="sum")
    clicks = fields.Integer(readonly=True, aggregator="sum")
    impressions = fields.Integer(readonly=True, aggregator="sum")
    platform_leads = fields.Integer(readonly=True, aggregator="sum",
                                    string="Leads (platform)")
    crm_leads = fields.Integer(readonly=True, aggregator="sum",
                               string="Leads (CRM)")
    expected_revenue = fields.Monetary(currency_field="currency_id",
                                       readonly=True, aggregator="sum")
    won_revenue = fields.Monetary(currency_field="currency_id", readonly=True,
                                  aggregator="sum")
    cost_per_crm_lead = fields.Float(readonly=True, aggregator="avg",
                                     string="Cost per CRM Lead")

    def init(self):
        drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                WITH spend AS (
                    SELECT i.utm_campaign_id, i.account_id, i.company_id,
                           i.currency_id,
                           date_trunc('month', i.date)::date AS month,
                           SUM(i.spend) AS spend,
                           SUM(i.clicks) AS clicks,
                           SUM(i.impressions) AS impressions,
                           SUM(i.leads_count) AS platform_leads
                      FROM ads_insight i
                     WHERE i.level = 'campaign'
                       AND i.utm_campaign_id IS NOT NULL
                     GROUP BY 1, 2, 3, 4, 5
                ), crm AS (
                    SELECT l.campaign_id,
                           date_trunc('month', l.create_date)::date AS month,
                           COUNT(*) AS crm_leads,
                           SUM(COALESCE(l.expected_revenue, 0)) AS expected_revenue,
                           SUM(CASE WHEN s.is_won
                                    THEN COALESCE(l.expected_revenue, 0)
                                    ELSE 0 END) AS won_revenue
                      FROM crm_lead l
                      LEFT JOIN crm_stage s ON s.id = l.stage_id
                     WHERE l.campaign_id IS NOT NULL
                     GROUP BY 1, 2
                )
                SELECT row_number() OVER (ORDER BY sp.month, sp.utm_campaign_id)
                           AS id,
                       sp.utm_campaign_id, sp.account_id, sp.company_id,
                       sp.currency_id, sp.month, sp.spend, sp.clicks,
                       sp.impressions, sp.platform_leads,
                       COALESCE(c.crm_leads, 0) AS crm_leads,
                       COALESCE(c.expected_revenue, 0) AS expected_revenue,
                       COALESCE(c.won_revenue, 0) AS won_revenue,
                       CASE WHEN COALESCE(c.crm_leads, 0) > 0
                            THEN sp.spend / c.crm_leads END AS cost_per_crm_lead
                  FROM spend sp
                  LEFT JOIN crm c ON c.campaign_id = sp.utm_campaign_id
                                 AND c.month = sp.month
            )""")
