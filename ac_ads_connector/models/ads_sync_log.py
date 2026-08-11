# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AdsSyncLog(models.Model):
    _name = "ads.sync.log"
    _description = "Ads Sync Run Log"
    _order = "id desc"

    account_id = fields.Many2one("ads.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    job_type = fields.Selection([
        ("structure", "Structure Pull"),
        ("insights", "Insights Pull"),
        ("leads", "Leads Pull"),
        ("push", "Push"),
        ("webhook", "Webhook"),
        ("compact", "Insight Compaction"),
        ("conversion", "Conversion Upload"),
        ("health", "Token Health"),
    ], required=True)
    trigger = fields.Selection(
        [("cron", "Scheduled"), ("manual", "Manual"), ("webhook", "Webhook")],
        default="cron", required=True)
    state = fields.Selection(
        [("running", "Running"), ("done", "Done"),
         ("partial", "Done with Errors"), ("error", "Failed")],
        default="running", required=True, index=True)
    started_at = fields.Datetime(default=fields.Datetime.now)
    finished_at = fields.Datetime()
    records_created = fields.Integer()
    records_updated = fields.Integer()
    records_skipped = fields.Integer()
    records_failed = fields.Integer()
    log = fields.Text(default="")
    error = fields.Text()
    payload_sample = fields.Json()
    async_report_ref = fields.Char(
        help="Meta async insights report_run_id being polled by a later cron pass.")
    watermark_before = fields.Char()
    watermark_after = fields.Char()

    # ------------------------------------------------------------- helper api
    @api.model
    def _job_start(self, account, job_type, trigger="cron"):
        return self.sudo().create({
            "account_id": account.id,
            "job_type": job_type,
            "trigger": trigger,
        })

    def _job_log(self, message):
        self.ensure_one()
        self.sudo().log = f"{self.log or ''}{fields.Datetime.now()} {message}\n"

    def _job_finish(self, state, error=None, **counters):
        self.ensure_one()
        vals = {"state": state, "finished_at": fields.Datetime.now()}
        if error:
            vals["error"] = str(error)[:2000]
        for key in ("records_created", "records_updated", "records_skipped",
                    "records_failed", "watermark_before", "watermark_after",
                    "async_report_ref", "payload_sample"):
            if key in counters:
                vals[key] = counters[key]
        self.sudo().write(vals)

    # ------------------------------------------------------------------ vacuum
    @api.model
    def _gc_sync_logs(self, days=90):
        cutoff = fields.Datetime.now() - timedelta(days=days)
        stale = self.sudo().search([("started_at", "<", cutoff)])
        count = len(stale)
        stale.unlink()
        if count:
            _logger.info("ads.sync.log GC removed %s rows", count)
