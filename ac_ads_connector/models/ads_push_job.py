# -*- coding: utf-8 -*-
"""Outbound mutation queue (wa.message pattern): every write-back to a platform
is a job row — retried by taxonomy, throttled, committed one by one so a later
failure can never roll back an already-applied remote mutation. Never raises
out of the cron; every failure is recorded on the job AND the source record."""
import logging
from datetime import timedelta

from odoo import _, api, fields, models

from ..tools.ads_api import AdsApiBase, AdsApiError

_logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
#: Meta allows 4 budget changes per hour per object; the 5th defers, never fails.
BUDGET_CHANGES_PER_HOUR = 4


class AdsPushJob(models.Model):
    _name = "ads.push.job"
    _description = "Ads Push Job"
    _order = "sequence, id"

    account_id = fields.Many2one(
        "ads.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    operation = fields.Selection([
        ("create", "Create on Platform"),
        ("update", "Update Fields"),
        ("set_status", "Set Status"),
        ("set_budget", "Set Budget"),
        ("audience_users", "Upload Audience Members"),
        ("conversion_upload", "Upload Click Conversion"),
    ], required=True)
    payload = fields.Json()
    state = fields.Selection([
        ("queued", "Queued"), ("in_progress", "In Progress"), ("done", "Done"),
        ("error", "Error"), ("cancel", "Cancelled"),
    ], default="queued", index=True, copy=False)
    failure_type = fields.Char(copy=False)
    failure_reason = fields.Text(copy=False)
    attempt = fields.Integer(default=0, copy=False)
    scheduled_at = fields.Datetime(default=fields.Datetime.now, index=True)
    result_external_id = fields.Char(copy=False)
    sequence = fields.Integer(default=10)

    # ------------------------------------------------------------------ queue
    @api.model
    def _enqueue(self, record, operation, payload=None):
        return self.sudo().create([{
            "account_id": record.account_id.id,
            "res_model": record._name,
            "res_id": record.id,
            "operation": operation,
            "payload": payload or {},
        }])

    @api.model
    def _push_cron(self, limit=20):
        jobs = self.sudo().search([
            ("state", "=", "queued"),
            ("scheduled_at", "<=", fields.Datetime.now()),
        ], order="sequence, id", limit=limit)
        for job in jobs:
            job._run_one()
            if self._commit_allowed():
                self.env.cr.commit()
        if len(jobs) == limit:
            self.env.ref("ac_ads_connector.ir_cron_ads_push_queue")._trigger()

    @api.model
    def _commit_allowed(self):
        return self.env["ads.sync.engine"]._commit_allowed()

    # ------------------------------------------------------------------- run
    def _run_one(self):
        """Execute one job. Never raises — failures land on the job/record."""
        self.ensure_one()
        record = self.env[self.res_model].sudo().browse(self.res_id)
        if not record.exists():
            self.sudo().write({"state": "cancel",
                               "failure_reason": "source record deleted"})
            return
        account = self.account_id
        cooldown = account._in_cooldown()
        if cooldown:
            self.sudo().write({"scheduled_at": account.cooldown_until})
            return
        if self.operation == "set_budget":
            next_slot = self._budget_slot_available()
            if next_slot:
                self.sudo().write({"scheduled_at": next_slot})
                return
        self.sudo().write({"state": "in_progress"})
        if "sync_status" in record._fields:
            record.with_context(skip_ads_sync=True).sync_status = "pushing"
        try:
            api_client = account._get_api()
            handler = getattr(self, f"_op_{self.operation}")
            handler(api_client, record)
        except AdsApiError as exc:
            self._handle_failure(record, exc)
            return
        except Exception as exc:  # noqa: BLE001 - queue must survive anything
            _logger.exception("push job %s crashed", self.id)
            self._handle_failure(record, AdsApiError(str(exc), "unknown"))
            return
        self.sudo().write({"state": "done", "failure_type": False,
                           "failure_reason": False})

    # ------------------------------------------------------------- operations
    def _op_create(self, api_client, record):
        payload = self.payload or {}
        result = api_client.create_object(payload["object_type"], record,
                                          payload.get("values") or {})
        vals = {
            "external_id": result["external_id"],
            "sync_status": "synced",
            "pending_changes": False,
            "last_pushed_snapshot": payload.get("values"),
            "push_error_message": False,
            "last_synced_at": fields.Datetime.now(),
        }
        if result.get("resource_name"):
            vals["provider_data"] = dict(record.provider_data or {},
                                         resource_name=result["resource_name"])
        if result.get("budget_resource") and "google_budget_resource" in record._fields:
            vals["google_budget_resource"] = result["budget_resource"]
        record.with_context(skip_ads_sync=True).write(vals)
        if result.get("creative_external_id") and getattr(record, "creative_id", None):
            record.creative_id.sudo().external_id = result["creative_external_id"]
        self.sudo().result_external_id = result["external_id"]

    def _op_set_status(self, api_client, record):
        payload = self.payload or {}
        api_client.set_status(payload["object_type"], record, payload["status"])
        # platform_status (verbatim/effective) is corrected by the next pull.
        # Other unpushed edits stay marked so they are not silently forgotten.
        record.with_context(skip_ads_sync=True).write({
            "status": payload["status"],
            "sync_status": "local_changes" if record.pending_changes else "synced",
            "last_synced_at": fields.Datetime.now(),
        })

    def _op_set_budget(self, api_client, record):
        payload = self.payload or {}
        api_client.set_budget(payload["object_type"], record,
                              payload["budget_type"], payload["amount"])
        remaining = [key for key in (record.pending_changes or [])
                     if key not in ("budget_type", "budget_amount")]
        record.with_context(skip_ads_sync=True).write({
            "budget_type": payload["budget_type"],
            "budget_amount": payload["amount"],
            "pending_changes": remaining or False,
            "sync_status": "queued" if remaining else "synced",
            "last_synced_at": fields.Datetime.now(),
        })

    def _op_update(self, api_client, record):
        payload = self.payload or {}
        values = payload.get("values") or {}
        changed = payload.get("changed") or sorted(values)
        api_client.update_object(payload["object_type"], record, values, changed)
        record.with_context(skip_ads_sync=True).write({
            "sync_status": "synced",
            "last_pushed_snapshot": values,
            "pending_changes": False,
            "push_error_message": False,
            "last_synced_at": fields.Datetime.now(),
        })

    #: platform batch cap for member uploads (Meta /users hard limit).
    AUDIENCE_BATCH = 10_000

    def _op_audience_users(self, api_client, record):
        """Normalize + hash partner PII at PUSH TIME only (the payload carries
        ids, never hashes) and upload in platform-sized batches."""
        payload = self.payload or {}
        partners = self.env["res.partner"].sudo().browse(
            payload.get("partner_ids") or []).exists()
        rows = []
        for partner in partners:
            row = {"email": AdsApiBase.hash_pii(partner.email, "email"),
                   "phone": AdsApiBase.hash_pii(partner.phone, "phone")}
            if row["email"] or row["phone"]:
                rows.append(row)
        for start in range(0, len(rows), self.AUDIENCE_BATCH):
            api_client.upload_audience_users(record,
                                             rows[start:start + self.AUDIENCE_BATCH])
        record.with_context(skip_ads_sync=True).write({
            "sync_status": "synced", "last_synced_at": fields.Datetime.now()})
        self.sudo().result_external_id = f"{len(rows)} members"

    def _op_conversion_upload(self, api_client, record):
        """Won CRM lead -> Google click conversion. Without a gclid, Enhanced
        Conversions for Leads kicks in: the lead's email/phone are hashed HERE
        (push time only, nothing persisted) as user identifiers."""
        payload = self.payload or {}
        conversion = {
            "gclid": payload.get("gclid"),
            "value": payload.get("value") or 0.0,
            "currency": payload.get("currency") or "USD",
            "datetime": payload["conversion_datetime"],
        }
        if not conversion["gclid"]:
            lead = self.env["crm.lead"].sudo().browse(
                payload.get("crm_lead_id") or 0).exists()
            conversion["email_hash"] = AdsApiBase.hash_pii(
                lead.email_from if lead else None, "email")
            conversion["phone_hash"] = AdsApiBase.hash_pii(
                lead.phone if lead else None, "phone")
            if not (conversion["email_hash"] or conversion["phone_hash"]):
                raise AdsApiError(
                    "No gclid and no hashable contact data — the conversion "
                    "cannot be attributed.", "validation")
        api_client.upload_click_conversions([conversion])
        self.sudo().result_external_id = conversion["gclid"] or "enhanced"
        if "sync_status" in record._fields:
            record.with_context(skip_ads_sync=True).sync_status = "synced"

    # -------------------------------------------------------------- failures
    def _handle_failure(self, record, exc):
        self.ensure_one()
        retryable = exc.failure_type in ("network", "recoverable", "rate_limit")
        vals = {"failure_type": exc.failure_type,
                "failure_reason": str(exc)[:2000],
                "attempt": self.attempt + 1}
        if retryable and self.attempt + 1 < MAX_ATTEMPTS:
            delay = min(2 ** (self.attempt + 1) * 60, 3600)
            if exc.failure_type == "rate_limit" and exc.retry_after:
                delay = max(delay, exc.retry_after)
            vals.update(state="queued",
                        scheduled_at=fields.Datetime.now() + timedelta(seconds=delay))
        else:
            vals["state"] = "error"
        self.sudo().write(vals)
        if "sync_status" in record._fields:
            record.with_context(skip_ads_sync=True).write({
                "sync_status": "push_error" if vals["state"] == "error" else "queued",
                "push_error_message": str(exc)[:2000],
            })
        if exc.failure_type in ("auth", "permission"):
            self.account_id._handle_auth_failure(exc)

    def button_retry(self):
        for job in self:
            if job.state == "error":
                job.sudo().write({"state": "queued",
                                  "scheduled_at": fields.Datetime.now()})
        self.env.ref("ac_ads_connector.ir_cron_ads_push_queue")._trigger()
        return True

    # -------------------------------------------------------------- throttle
    def _budget_slot_available(self):
        """None when a budget change may run now; else the next free slot."""
        self.ensure_one()
        window_start = fields.Datetime.now() - timedelta(hours=1)
        recent = self.sudo().search([
            ("res_model", "=", self.res_model),
            ("res_id", "=", self.res_id),
            ("operation", "=", "set_budget"),
            ("state", "=", "done"),
            ("write_date", ">=", window_start),
        ], order="write_date")
        if len(recent) < BUDGET_CHANGES_PER_HOUR:
            return None
        return recent[0].write_date + timedelta(hours=1)