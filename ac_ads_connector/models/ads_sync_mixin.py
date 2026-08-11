# -*- coding: utf-8 -*-
from datetime import date, datetime

from odoo import _, fields, models
from odoo.exceptions import UserError


class AdsSyncMixin(models.AbstractModel):
    """Shared state for every record mirrored from (or pushed to) an ad platform.

    The write-back state machine lives here so it is defined — and tested — in
    exactly one place:

        draft -> queued -> pushing -> synced
        synced --user edit of a pushable field--> local_changes
        local_changes + remote change            -> conflict (remote staged in
                                                    remote_snapshot, never applied)
        any push failure                         -> push_error

    Pull passes ALWAYS write with ``skip_ads_sync`` in the context; that context
    key is the echo guard in both directions (a pull must not look like a user
    edit, and a post-push write-back must not re-dirty the record).

    ``_ADS_PUSHABLE_FIELDS`` stays empty until the write-back phase populates it
    per model — until then the dirty-tracking below is inert by construction.
    """

    _name = "ads.sync.mixin"
    _description = "Ads Sync State"

    _ADS_PUSHABLE_FIELDS = frozenset()

    external_id = fields.Char(index=True, copy=False, readonly=True,
                              help="Identifier of this record on the ad platform.")
    sync_status = fields.Selection([
        ("draft", "Draft"),
        ("queued", "Queued"),
        ("pushing", "Pushing"),
        ("synced", "Synced"),
        ("local_changes", "Local Changes"),
        ("conflict", "Conflict"),
        ("push_error", "Push Error"),
    ], default="synced", copy=False, index=True)
    raw_payload = fields.Json(copy=False)
    provider_data = fields.Json(copy=False)
    remote_snapshot = fields.Json(
        copy=False,
        help="Remote values staged during a conflict — applied only via 'Keep Remote'.")
    last_pushed_snapshot = fields.Json(copy=False)
    remote_updated_at = fields.Datetime(copy=False, readonly=True)
    last_synced_at = fields.Datetime(copy=False, readonly=True)
    push_error_message = fields.Text(copy=False, readonly=True)
    sync_error = fields.Char(copy=False, readonly=True)
    pending_changes = fields.Json(
        copy=False, help="Pushable field names edited locally since the last push.")

    def write(self, vals):
        """Track WHICH pushable fields the user edits (that set later drives
        the push payload / update masks) and flip clean records to
        local_changes. Pull/push writes bypass via skip_ads_sync."""
        changed_keys = (
            set() if self.env.context.get("skip_ads_sync") or "sync_status" in vals
            else self._ADS_PUSHABLE_FIELDS.intersection(vals))
        if not changed_keys:
            return super().write(vals)
        tracked = self.filtered(
            lambda r: r.external_id and r.sync_status in (
                "synced", "local_changes", "queued", "push_error"))
        for record in tracked:
            merged = sorted(set(record.pending_changes or []) | changed_keys)
            extra = {"pending_changes": merged}
            if record.sync_status == "synced":
                extra["sync_status"] = "local_changes"
            super(AdsSyncMixin, record).write(dict(vals, **extra))
        remaining = self - tracked
        if remaining:
            super(AdsSyncMixin, remaining).write(vals)
        return True

    # -------------------------------------------------------------- staging
    @staticmethod
    def _json_safe(vals):
        """Snapshot values so they survive a Json column AND can later be fed
        back into write() unchanged (datetimes as Odoo strings)."""
        safe = {}
        for key, value in vals.items():
            if isinstance(value, datetime):
                safe[key] = fields.Datetime.to_string(value)
            elif isinstance(value, date):
                safe[key] = fields.Date.to_string(value)
            else:
                safe[key] = value
        return safe

    def _stage_remote(self, vals, conflict):
        """Store the remote view without applying it; flip to conflict when the
        remote actually diverges from pending local work."""
        self.ensure_one()
        pushable = {key: value for key, value in vals.items()
                    if key in self._ADS_PUSHABLE_FIELDS}
        update = {
            "remote_snapshot": self._json_safe(pushable),
            "remote_updated_at": vals.get("remote_updated_at"),
        }
        if conflict and self.sync_status in ("local_changes", "push_error"):
            update["sync_status"] = "conflict"
        self.with_context(skip_ads_sync=True).write(update)
        if update.get("sync_status") == "conflict":
            self._notify_conflict()

    def _remote_differs(self, vals):
        """True when any pushable field differs between the remote row and the
        record's CURRENT (locally edited) values."""
        self.ensure_one()
        for key in self._ADS_PUSHABLE_FIELDS.intersection(vals):
            local = self[key]
            remote = vals[key]
            if isinstance(local, models.BaseModel):
                continue
            if (local or False) != (remote or False):
                return True
        return False

    def _notify_conflict(self):
        self.ensure_one()
        if hasattr(self, "message_post"):
            self.message_post(body=_(
                "Both Odoo and the ad platform changed this record. Review the "
                "remote values and resolve with Keep Local or Keep Remote."))
        if hasattr(self, "activity_schedule"):
            try:
                self.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Sync conflict on %s", self.display_name),
                    user_id=self.env.ref("base.user_admin").id)
            except Exception:  # noqa: BLE001 - alerting must not break the pull
                pass

    # -------------------------------------------------------------- resolve
    def action_resolve_keep_remote(self):
        for record in self:
            if record.sync_status != "conflict":
                continue
            snapshot = record.remote_snapshot or {}
            vals = {key: value for key, value in snapshot.items()
                    if key in record._ADS_PUSHABLE_FIELDS}
            record.with_context(skip_ads_sync=True).write(dict(
                vals, sync_status="synced", remote_snapshot=False,
                pending_changes=False, push_error_message=False))
        return True

    def action_resolve_keep_local(self):
        for record in self:
            if record.sync_status != "conflict":
                continue
            record.with_context(skip_ads_sync=True).write({
                "sync_status": "local_changes", "remote_snapshot": False})
            record.action_push()
        return True

    # ----------------------------------------------------------------- push
    def _push_object_type(self):
        return {"ads.campaign": "campaign", "ads.adset": "adset",
                "ads.ad": "ad"}.get(self._name)

    def _push_payload(self):
        self.ensure_one()
        changed = sorted(self.pending_changes or self._ADS_PUSHABLE_FIELDS)
        values = {}
        for key in changed:
            value = self[key]
            if isinstance(value, models.BaseModel):
                continue
            values[key] = value
        return {"object_type": self._push_object_type(),
                "external_id": self.external_id,
                "values": self._json_safe(values),
                "changed": changed}

    _BUDGET_KEYS = frozenset({"budget_type", "budget_amount"})

    # --------------------------------------------------- create (draft) hooks
    def _validate_for_push(self):
        raise UserError(_("Records of this type cannot be created from Odoo."))

    def _create_push_payload(self):
        raise UserError(_("Records of this type cannot be created from Odoo."))

    def action_push(self):
        """Queue a push. Drafts become platform objects (always created
        PAUSED); edited records push their changed fields, with budget changes
        travelling as their own throttled set_budget job. A single record's
        jobs run inline; batches drain through the cron."""
        drafts = self.filtered(
            lambda r: not r.external_id and r.sync_status in ("draft", "push_error"))
        pushable = self.filtered(
            lambda r: r.external_id
            and r.sync_status in ("local_changes", "push_error", "queued"))
        if not drafts and not pushable:
            raise UserError(_("Nothing to push: no draft and no pending local changes."))
        jobs = self.env["ads.push.job"]
        for record in drafts:
            record._validate_for_push()
            payload = record._create_push_payload()
            record.with_context(skip_ads_sync=True).sync_status = "queued"
            jobs |= self.env["ads.push.job"]._enqueue(record, "create", payload)
        for record in pushable:
            record.with_context(skip_ads_sync=True).sync_status = "queued"
            payload = record._push_payload()
            budget_keys = self._BUDGET_KEYS.intersection(payload["changed"])
            if budget_keys:
                jobs |= self.env["ads.push.job"]._enqueue(record, "set_budget", {
                    "object_type": record._push_object_type(),
                    "external_id": record.external_id,
                    "budget_type": record.budget_type,
                    "amount": record.budget_amount,
                })
            payload["values"] = {key: value for key, value in payload["values"].items()
                                 if key not in self._BUDGET_KEYS}
            payload["changed"] = [key for key in payload["changed"]
                                  if key not in self._BUDGET_KEYS]
            if payload["values"]:
                jobs |= self.env["ads.push.job"]._enqueue(record, "update", payload)
        if len(drafts) + len(pushable) == 1:
            # the interactive path: one record's jobs run inline, in order
            for job in jobs:
                job._run_one()
        else:
            self.env.ref("ac_ads_connector.ir_cron_ads_push_queue")._trigger()
        return True

    def action_pause(self):
        return self._push_status("paused")

    def action_activate(self):
        return self._push_status("enabled")

    def _push_status(self, status):
        for record in self:
            if not record.external_id:
                raise UserError(_("%s was never pushed to the platform.",
                                  record.display_name))
            job = self.env["ads.push.job"]._enqueue(
                record, "set_status",
                {"object_type": record._push_object_type(),
                 "external_id": record.external_id, "status": status})
            job._run_one()
        return True
