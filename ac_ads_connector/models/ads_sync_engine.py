# -*- coding: utf-8 -*-
"""Pull orchestration. One engine, provider differences already flattened into
normalized dicts by the tools layer.

Guarantees:
  * idempotent — upsert keyed on (account_id, external_id)
  * per-record isolation — one poison row marks itself failed, the page goes on
  * per-account isolation — one broken account never blocks the others
  * resumable — a killed run restarts at the failed entity (full re-pull of one
    entity is cheap and idempotent; struct_cursor records where it died)
  * echo-safe — every write carries skip_ads_sync so pulls never look like
    user edits to the write-back machinery
  * clobber-safe — records with local pending state (draft/queued/pushing/
    local_changes/push_error) only get the remote values STAGED, never applied
"""
import logging
import threading
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import config

from ..tools.ads_api import AdsApiError

_logger = logging.getLogger(__name__)

#: entity passes in dependency order: parents before children.
STRUCTURE_PASSES = [
    ("ads.campaign", "list_campaigns", None),
    ("ads.adset", "list_adsets", "campaign_external_id"),
    ("ads.ad", "list_ads", "adset_external_id"),
    ("ads.audience", "list_audiences", None),
]
#: sync_status values whose records a pull must not overwrite.
LOCAL_PENDING = ("draft", "queued", "pushing", "local_changes", "push_error")


class AdsSyncEngine(models.AbstractModel):
    _name = "ads.sync.engine"
    _description = "Ads Sync Engine"

    @api.model
    def _commit_allowed(self):
        testing = config["test_enable"] or getattr(
            threading.current_thread(), "testing", False)
        return not testing

    # ----------------------------------------------------------------- cron
    @api.model
    def _cron_sync_structure(self):
        accounts = self.env["ads.account"].search([("state", "=", "connected")])
        for account in accounts:
            job = self.env["ads.sync.log"]._job_start(account, "structure", "cron")
            try:
                counters = self._sync_structure_account(account, job)
                state = "partial" if counters["records_failed"] else "done"
                job._job_finish(state, **counters)
            except AdsApiError as exc:
                job._job_finish("error", error=exc)
                if exc.failure_type in ("auth", "permission"):
                    account._handle_auth_failure(exc)
                else:
                    _logger.warning("structure sync aborted for %s: %s", account.name, exc)
            except Exception as exc:  # noqa: BLE001 - account isolation
                _logger.exception("structure sync crashed for %s", account.name)
                job._job_finish("error", error=exc)
            if self._commit_allowed():
                self.env.cr.commit()

    # -------------------------------------------------------------- one account
    @api.model
    def _sync_structure_account(self, account, job):
        api_client = account._get_api()
        counters = dict(records_created=0, records_updated=0,
                        records_skipped=0, records_failed=0)
        for model_name, method_name, parent_key in STRUCTURE_PASSES:
            method = getattr(api_client, method_name, None)
            if not method:
                continue
            account.sudo().struct_cursor = {"entity": model_name}
            pages = method(since=None)
            for rows in pages:
                page_counts = self._upsert_page(account, model_name, rows, parent_key)
                for key in counters:
                    counters[key] += page_counts[key]
                if self._commit_allowed():
                    self.env.cr.commit()
            job._job_log(f"{model_name}: pass complete "
                         f"({counters['records_created']}c/{counters['records_updated']}u"
                         f"/{counters['records_skipped']}s/{counters['records_failed']}f)")
        account.sudo().write({
            "struct_cursor": False,
            "struct_synced_at": fields.Datetime.now(),
        })
        return counters

    # ------------------------------------------------------------------ upsert
    @api.model
    def _upsert_page(self, account, model_name, rows, parent_key=None):
        """Idempotent upsert of one page of normalized rows."""
        Model = self.env[model_name].with_context(skip_ads_sync=True).sudo()
        counts = dict(records_created=0, records_updated=0,
                      records_skipped=0, records_failed=0)
        external_ids = [row["external_id"] for row in rows if row.get("external_id")]
        existing = {
            record.external_id: record
            for record in Model.with_context(active_test=False).search([
                ("account_id", "=", account.id),
                ("external_id", "in", external_ids),
            ])
        }
        parents = self._parent_map(account, rows, parent_key) if parent_key else {}
        for row in rows:
            try:
                result = self._upsert_row(account, Model, row, existing,
                                          parents, parent_key)
                counts[result] += 1
            except Exception as exc:  # noqa: BLE001 - record isolation
                counts["records_failed"] += 1
                _logger.exception("upsert failed for %s %s", model_name,
                                  row.get("external_id"))
                record = existing.get(row.get("external_id"))
                if record:
                    record.write({"sync_error": str(exc)[:500]})
        return counts

    @api.model
    def _parent_map(self, account, rows, parent_key):
        parent_model = {"campaign_external_id": "ads.campaign",
                        "adset_external_id": "ads.adset"}[parent_key]
        wanted = {row.get(parent_key) for row in rows if row.get(parent_key)}
        records = self.env[parent_model].sudo().with_context(active_test=False).search([
            ("account_id", "=", account.id),
            ("external_id", "in", list(wanted)),
        ])
        return {record.external_id: record for record in records}

    @api.model
    def _upsert_row(self, account, Model, row, existing, parents, parent_key):
        record = existing.get(row.get("external_id"))
        if parent_key:
            parent = parents.get(row.get(parent_key))
            if not parent:
                # Child arrived before its parent (or parent filtered out
                # remotely) — next full pass will pick it up.
                return "records_skipped"
        vals = Model._prepare_sync_vals(account, row)
        if parent_key:
            field = "campaign_id" if parent_key == "campaign_external_id" else "adset_id"
            vals[field] = parent.id
        if record:
            if record.sync_status in LOCAL_PENDING:
                # Never clobber local pending work: stage the remote view, and
                # flip to conflict when the remote genuinely diverged from the
                # locally edited values.
                conflict = (record.sync_status in ("local_changes", "push_error")
                            and record._remote_differs(vals))
                record._stage_remote(vals, conflict)
                return "records_skipped"
            record.write(vals)
            return "records_updated"
        Model.create([vals])
        return "records_created"

    # ---------------------------------------------------------------- insights
    #: Meta restates attribution for up to 28 days, then rows freeze.
    RESTATEMENT_DAYS = 28
    BACKFILL_CHUNK_DAYS = 7

    @api.model
    def _insight_levels(self, account):
        return {
            "campaign": ["campaign"],
            "adset": ["campaign", "adset"],
            "ad": ["campaign", "adset", "ad"],
        }[account.insight_sync_level]

    @api.model
    def _cron_sync_insights(self, today_only=False):
        accounts = self.env["ads.account"].search([("state", "=", "connected")])
        for account in accounts:
            job = self.env["ads.sync.log"]._job_start(account, "insights", "cron")
            try:
                counters = self._sync_insights_account(account, today_only=today_only)
                state = "partial" if counters["records_failed"] else "done"
                job._job_finish(state, **counters)
            except AdsApiError as exc:
                job._job_finish("error", error=exc)
                if exc.failure_type in ("auth", "permission"):
                    account._handle_auth_failure(exc)
                else:
                    _logger.warning("insight sync aborted for %s: %s", account.name, exc)
            except Exception as exc:  # noqa: BLE001 - account isolation
                _logger.exception("insight sync crashed for %s", account.name)
                job._job_finish("error", error=exc)
            if self._commit_allowed():
                self.env.cr.commit()

    @api.model
    def _sync_insights_account(self, account, today_only=False):
        today = fields.Date.today()
        api_client = account._get_api()
        counters = dict(records_created=0, records_updated=0,
                        records_skipped=0, records_failed=0)
        self._poll_pending_insight_jobs(account, api_client, counters)
        since = today if today_only else today - timedelta(days=self.RESTATEMENT_DAYS)
        for level in self._insight_levels(account):
            self._pull_insights_window(account, api_client, level, since, today, counters)
        if not today_only:
            self._advance_backfill(account, api_client, counters)
            account.sudo().insights_synced_until = today
        return counters

    @api.model
    def _pull_insights_window(self, account, api_client, level, since, until, counters):
        """Pull one level/date-window; a single day too big even after client-side
        splitting escalates to an async report job polled by later cron runs."""
        Insight = self.env["ads.insight"]
        try:
            for rows in api_client.iter_insights(level, since, until):
                page_counts = Insight._upsert_from_rows(account, level, rows)
                for key in counters:
                    counters[key] += page_counts[key]
                if self._commit_allowed():
                    self.env.cr.commit()
        except AdsApiError as exc:
            if exc.failure_type != "too_much_data":
                raise
            report_ref = api_client.start_insights_job(level, since, until)
            self.env["ads.sync.log"].sudo().create({
                "account_id": account.id,
                "job_type": "insights",
                "trigger": "cron",
                "state": "running",
                "async_report_ref": report_ref,
                "payload_sample": {"level": level, "since": since.isoformat(),
                                   "until": until.isoformat()},
                "log": f"async insights job for level={level} "
                       f"{since} -> {until}\n",
            })

    @api.model
    def _poll_pending_insight_jobs(self, account, api_client, counters):
        Insight = self.env["ads.insight"]
        pending = self.env["ads.sync.log"].sudo().search([
            ("account_id", "=", account.id),
            ("job_type", "=", "insights"),
            ("state", "=", "running"),
            ("async_report_ref", "!=", False),
        ])
        for job in pending:
            status = api_client.poll_insights_job(job.async_report_ref)
            if status["status"] == "Job Completed":
                level = (job.payload_sample or {}).get("level", "campaign")
                for rows in api_client.iter_insights_results(job.async_report_ref):
                    page_counts = Insight._upsert_from_rows(account, level, rows)
                    for key in counters:
                        counters[key] += page_counts[key]
                job._job_finish("done")
            elif status["status"] in ("Job Failed", "Job Skipped"):
                job._job_finish("error", error=f"async status: {status['status']}")
            # otherwise: still running — checked again next cron pass

    @api.model
    def _advance_backfill(self, account, api_client, counters):
        """Walk history backwards one chunk per cron tick; re-trigger the cron
        while work remains so the backfill drains without a long transaction."""
        today = fields.Date.today()
        floor = today - timedelta(days=account.insight_backfill_days)
        cursor = account.insight_backfill_date or (
            today - timedelta(days=self.RESTATEMENT_DAYS))
        if cursor <= floor:
            return
        chunk_until = cursor - timedelta(days=1)
        chunk_since = max(floor, cursor - timedelta(days=self.BACKFILL_CHUNK_DAYS))
        for level in self._insight_levels(account):
            self._pull_insights_window(account, api_client, level,
                                       chunk_since, chunk_until, counters)
        account.sudo().insight_backfill_date = chunk_since
        if chunk_since > floor:
            self.env.ref("ac_ads_connector.ir_cron_ads_sync_insights")._trigger()

    # ------------------------------------------------------------------- leads
    FORM_REFRESH_DAYS = 7
    #: overlap re-queried below the watermark — at-least-once + id-dedup makes
    #: the replay free, and no platform-side lag can open a gap.
    LEAD_OVERLAP_DAYS = 1
    #: conservative Google retention (sources conflict 30 vs 60 days).
    LEAD_DEFAULT_LOOKBACK_DAYS = 30

    @api.model
    def _cron_sync_leads(self):
        accounts = self.env["ads.account"].search([
            ("state", "=", "connected"), ("lead_sync_enabled", "=", True)])
        for account in accounts:
            job = self.env["ads.sync.log"]._job_start(account, "leads", "cron")
            try:
                counters = self._sync_leads_account(account, job)
                state = "partial" if counters["records_failed"] else "done"
                job._job_finish(state, **counters)
            except AdsApiError as exc:
                job._job_finish("error", error=exc)
                if exc.failure_type in ("auth", "permission"):
                    account._handle_auth_failure(exc)
                else:
                    _logger.warning("lead sync aborted for %s: %s", account.name, exc)
            except Exception as exc:  # noqa: BLE001 - account isolation
                _logger.exception("lead sync crashed for %s", account.name)
                job._job_finish("error", error=exc)
            if self._commit_allowed():
                self.env.cr.commit()
        self.env["ads.lead"]._process_pending()

    @api.model
    def _sync_leads_account(self, account, job):
        api_client = account._get_api()
        counters = dict(records_created=0, records_updated=0,
                        records_skipped=0, records_failed=0)
        self._refresh_lead_forms(account, api_client, job)
        self._fetch_pending_stubs(account, api_client, counters)
        forms = self.env["ads.lead.form"].sudo().search([
            ("account_id", "=", account.id)])
        forms_by_ext = {form.external_id: form for form in forms}
        if getattr(api_client, "LEADS_PER_FORM", True):
            for form in forms:
                self._pull_form_leads(account, api_client, form, counters)
        else:
            # one query serves every form; route rows by form_external_id.
            # The window starts at the OLDEST form watermark (any form never
            # synced -> full conservative lookback).
            marks = forms.mapped("leads_synced_until")
            oldest = min(marks) if marks and all(marks) else None
            since = self._lead_since(oldest)
            watermarks = {}
            for rows in api_client.iter_leads(since=since):
                for row in rows:
                    form = forms_by_ext.get(row.get("form_external_id"))
                    if not form:
                        counters["records_skipped"] += 1
                        continue
                    created, _record = self.env["ads.lead"]._upsert_from_row(
                        account, form, row)
                    counters["records_created" if created else "records_updated"] += 1
                    if row.get("created_time"):
                        current = watermarks.get(form.id)
                        if not current or row["created_time"] > current:
                            watermarks[form.id] = row["created_time"]
                if self._commit_allowed():
                    self.env.cr.commit()
            for form_id, watermark in watermarks.items():
                self.env["ads.lead.form"].browse(form_id).sudo(
                ).leads_synced_until = watermark
        job._job_log(f"leads pass complete ({counters['records_created']} new)")
        return counters

    @api.model
    def _refresh_lead_forms(self, account, api_client, job):
        stale = (not account.forms_synced_at
                 or account.forms_synced_at
                 < fields.Datetime.now() - timedelta(days=self.FORM_REFRESH_DAYS))
        if not stale:
            return
        for row in api_client.list_lead_forms():
            self.env["ads.lead.form"]._upsert_from_row(account, row)
        account.sudo().forms_synced_at = fields.Datetime.now()
        job._job_log("lead forms refreshed")

    @api.model
    def _fetch_pending_stubs(self, account, api_client, counters):
        """Webhook deliveries only stub the lead (no PII travels through the
        public route); this fills them via the Graph follow-up fetch."""
        get_lead = getattr(api_client, "get_lead", None)
        if not get_lead:
            return
        stubs = self.env["ads.lead"].sudo().search([
            ("account_id", "=", account.id), ("state", "=", "fetch_pending")])
        for stub in stubs:
            try:
                row = get_lead(stub.external_id)
            except AdsApiError as exc:
                if exc.failure_type in ("auth", "permission", "rate_limit"):
                    raise
                counters["records_failed"] += 1
                stub.write({"error_message": str(exc)[:500]})
                continue
            form = stub.form_id
            if not form and row.get("form_external_id"):
                form = self.env["ads.lead.form"].sudo().search([
                    ("account_id", "=", account.id),
                    ("external_id", "=", row["form_external_id"])], limit=1)
                if form:
                    stub.write({"form_id": form.id})
            stub._fill_from_row(account, form, row)
            counters["records_updated"] += 1

    @api.model
    def _lead_since(self, watermark):
        if watermark:
            return watermark - timedelta(days=self.LEAD_OVERLAP_DAYS)
        return fields.Datetime.now() - timedelta(days=self.LEAD_DEFAULT_LOOKBACK_DAYS)

    @api.model
    def _pull_form_leads(self, account, api_client, form, counters):
        since = self._lead_since(form.leads_synced_until)
        watermark = form.leads_synced_until
        for rows in api_client.iter_leads(form.external_id, since=since):
            for row in rows:
                created, _record = self.env["ads.lead"]._upsert_from_row(
                    account, form, row)
                counters["records_created" if created else "records_updated"] += 1
                if row.get("created_time") and (
                        not watermark or row["created_time"] > watermark):
                    watermark = row["created_time"]
            if self._commit_allowed():
                self.env.cr.commit()
        if watermark and watermark != form.leads_synced_until:
            form.sudo().leads_synced_until = watermark

    @api.model
    def _sync_leads_now(self, account):
        job = self.env["ads.sync.log"]._job_start(account, "leads", "manual")
        try:
            counters = self._sync_leads_account(account, job)
        except AdsApiError as exc:
            job._job_finish("error", error=exc)
            if exc.failure_type in ("auth", "permission"):
                account._handle_auth_failure(exc)
            return account._notify(str(exc), kind="danger")
        state = "partial" if counters["records_failed"] else "done"
        job._job_finish(state, **counters)
        self.env["ads.lead"]._process_pending()
        return account._notify(
            f"Leads synced: {counters['records_created']} new submissions.",
            kind="success")

    @api.model
    def _sync_insights_now(self, account):
        """Interactive 'Sync Insights' — notification style, never raises."""
        job = self.env["ads.sync.log"]._job_start(account, "insights", "manual")
        try:
            counters = self._sync_insights_account(account)
        except AdsApiError as exc:
            job._job_finish("error", error=exc)
            if exc.failure_type in ("auth", "permission"):
                account._handle_auth_failure(exc)
            return account._notify(str(exc), kind="danger")
        state = "partial" if counters["records_failed"] else "done"
        job._job_finish(state, **counters)
        return account._notify(
            f"Insights synced: {counters['records_created']} created, "
            f"{counters['records_updated']} updated.",
            kind="warning" if counters["records_failed"] else "success")

    # ---------------------------------------------------------------- manual
    @api.model
    def _sync_now(self, account):
        """Interactive 'Sync Now': returns a notification, never raises (a
        raised UserError would roll back everything already synced)."""
        job = self.env["ads.sync.log"]._job_start(account, "structure", "manual")
        try:
            counters = self._sync_structure_account(account, job)
        except AdsApiError as exc:
            job._job_finish("error", error=exc)
            if exc.failure_type in ("auth", "permission"):
                account._handle_auth_failure(exc)
            return account._notify(str(exc), kind="danger")
        state = "partial" if counters["records_failed"] else "done"
        job._job_finish(state, **counters)
        return account._notify(
            f"Structure synced: {counters['records_created']} created, "
            f"{counters['records_updated']} updated, "
            f"{counters['records_skipped']} skipped, "
            f"{counters['records_failed']} failed.",
            kind="warning" if counters["records_failed"] else "success")
