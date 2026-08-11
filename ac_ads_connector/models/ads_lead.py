# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools import email_normalize

_logger = logging.getLogger(__name__)


class AdsLead(models.Model):
    """Staging record for every lead delivered by a platform. Idempotent on
    (external_id, account_id) — webhook and polling overlap is expected and
    free. The processing pipeline turns rows into crm.lead with UTM
    attribution and duplicate detection; nothing is ever silently dropped
    (skipped/merged rows stay visible and reprocessable)."""

    _name = "ads.lead"
    _description = "Platform Lead (staging)"
    _order = "submitted_at desc, id desc"

    account_id = fields.Many2one(
        "ads.account", required=True, ondelete="cascade", index=True)
    provider = fields.Selection(related="account_id.provider", store=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    form_id = fields.Many2one("ads.lead.form", ondelete="set null", index=True)
    external_id = fields.Char(index=True, copy=False, readonly=True, required=True)
    submitted_at = fields.Datetime(readonly=True)
    field_data = fields.Json(readonly=True)
    raw_payload = fields.Json(readonly=True)
    platform_refs = fields.Json(
        readonly=True, help="Platform ids (campaign/adset/ad) for re-resolution "
                            "when the structure mirror lagged behind the lead.")
    is_organic = fields.Boolean(readonly=True)
    gclid = fields.Char(readonly=True, index=True)

    contact_name = fields.Char(readonly=True)
    email = fields.Char(readonly=True)
    phone = fields.Char(readonly=True)

    campaign_id = fields.Many2one("ads.campaign", ondelete="set null", index=True)
    adset_id = fields.Many2one("ads.adset", ondelete="set null")
    ad_id = fields.Many2one("ads.ad", ondelete="set null")

    state = fields.Selection([
        ("new", "New"),
        ("fetch_pending", "Fetch Pending"),
        ("processed", "Lead Created"),
        ("merged", "Merged into Existing"),
        ("skipped", "Skipped (Duplicate)"),
        ("error", "Error"),
    ], default="new", copy=False, index=True)
    error_message = fields.Text(readonly=True)
    crm_lead_id = fields.Many2one(
        "crm.lead", copy=False, ondelete="set null", index="btree_not_null")
    duplicate_lead_id = fields.Many2one("crm.lead", copy=False, ondelete="set null")

    _external_unique = models.UniqueIndex("(external_id, account_id)")

    # ------------------------------------------------------------------ upsert
    @api.model
    def _upsert_from_row(self, account, form, row):
        """Returns (created: bool, record). Existing external ids are left
        untouched — at-least-once delivery makes replays normal."""
        existing = self.sudo().search([
            ("account_id", "=", account.id),
            ("external_id", "=", row["external_id"]),
        ], limit=1)
        if existing:
            if existing.state == "fetch_pending" and row.get("fields"):
                existing._fill_from_row(account, form, row)
            return False, existing
        record = self.sudo().create([{
            "account_id": account.id,
            "form_id": form.id if form else False,
            "external_id": row["external_id"],
            "state": "new" if row.get("fields") else "fetch_pending",
        }])
        record._fill_from_row(account, form, row)
        return True, record

    def _fill_from_row(self, account, form, row):
        self.ensure_one()
        refs = {
            "campaign": row.get("campaign_external_id"),
            "adset": row.get("adset_external_id"),
            "ad": row.get("ad_external_id"),
        }
        extracted = self._extract_contact(row.get("fields") or [], form)
        self.sudo().write({
            "submitted_at": row.get("created_time"),
            "field_data": row.get("fields"),
            "raw_payload": row.get("raw"),
            "platform_refs": refs,
            "is_organic": bool(row.get("is_organic")),
            "gclid": row.get("gclid"),
            "state": "new" if row.get("fields") else "fetch_pending",
            **extracted,
        })
        self._resolve_attribution()

    @api.model
    def _extract_contact(self, field_rows, form):
        """Pull name/email/phone out of the answers for list readability and
        dedup — independent of the configurable crm mapping."""
        values = {}
        for entry in field_rows:
            key = (entry.get("key") or "").lower()
            answer = ", ".join(str(v) for v in (entry.get("values") or []) if v)
            if not answer:
                continue
            if key in ("email", "work_email") and not values.get("email"):
                values["email"] = answer
            elif key in ("phone_number", "phone", "work_phone_number") \
                    and not values.get("phone"):
                values["phone"] = answer
            elif key in ("full_name", "first_name") and not values.get("contact_name"):
                values["contact_name"] = answer
        return values

    def _resolve_attribution(self):
        for lead in self:
            refs = lead.platform_refs or {}
            vals = {}
            for key, model, field in (("campaign", "ads.campaign", "campaign_id"),
                                      ("adset", "ads.adset", "adset_id"),
                                      ("ad", "ads.ad", "ad_id")):
                if refs.get(key) and not lead[field]:
                    record = self.env[model].sudo().with_context(
                        active_test=False).search([
                            ("account_id", "=", lead.account_id.id),
                            ("external_id", "=", str(refs[key]))], limit=1)
                    if record:
                        vals[field] = record.id
            if vals:
                lead.sudo().write(vals)

    # -------------------------------------------------------------- processing
    @api.model
    def _process_pending(self, limit=200):
        """Cron tail: auto-convert new staged leads for accounts that opted in."""
        pending = self.sudo().search([
            ("state", "=", "new"),
            ("account_id.lead_auto_convert", "=", True),
            ("form_id.create_crm_lead", "!=", False),
        ], limit=limit, order="id")
        pending.action_process()
        if len(pending) == limit:
            self.env.ref("ac_ads_connector.ir_cron_ads_sync_leads")._trigger()
        return len(pending)

    def action_process(self, force=False):
        """Convert staged rows to crm.lead. Never raises across records."""
        allowed = ("new", "error", "skipped") if force else ("new",)
        for lead in self:
            if lead.state not in allowed:
                continue
            try:
                lead._resolve_attribution()
                lead._process_one()
            except Exception as exc:  # noqa: BLE001 - record isolation
                _logger.exception("lead processing failed for %s", lead.external_id)
                lead.sudo().write({"state": "error",
                                   "error_message": str(exc)[:2000]})
        return True

    def _process_one(self):
        self.ensure_one()
        account = self.account_id
        duplicate = self._find_duplicate()
        if duplicate and account.duplicate_policy == "skip":
            self.sudo().write({"state": "skipped", "duplicate_lead_id": duplicate.id})
            return
        if duplicate and account.duplicate_policy == "log_on_existing":
            duplicate.message_post(body=self._duplicate_note())
            self.sudo().write({"state": "merged", "duplicate_lead_id": duplicate.id,
                               "crm_lead_id": duplicate.id})
            return
        crm_lead = self.env["crm.lead"].sudo().with_context(
            mail_create_nosubscribe=True, mail_create_nolog=True,
        ).create(self._prepare_crm_lead_vals())
        self.sudo().write({"state": "processed", "crm_lead_id": crm_lead.id,
                           "duplicate_lead_id": duplicate.id if duplicate else False})

    def _duplicate_note(self):
        self.ensure_one()
        return _(
            "New ad lead submission matched this lead: form %(form)s, "
            "campaign %(campaign)s, submitted %(when)s.",
            form=self.form_id.name or "?",
            campaign=self.campaign_id.title or "?",
            when=self.submitted_at or "?")

    # ------------------------------------------------------------------- dedup
    @staticmethod
    def _sanitize_phone(phone, country=None):
        """E.164 via phonenumbers (what crm.lead.phone_sanitized stores)."""
        if not phone:
            return None
        try:
            import phonenumbers  # noqa: PLC0415 - odoo ships it with phone_validation
            parsed = phonenumbers.parse(phone, country.code if country else None)
            if phonenumbers.is_possible_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164)
        except Exception:  # noqa: BLE001 - unparseable numbers just don't dedup
            return None
        return None

    def _find_duplicate(self):
        self.ensure_one()
        account = self.account_id
        mode = account.lead_dedup_mode
        if mode == "none":
            return self.env["crm.lead"]
        clauses = []
        normalized = email_normalize(self.email) if self.email else None
        if normalized:
            clauses.append(("email_normalized", "=", normalized))
        if mode == "email_phone":
            sanitized = self._sanitize_phone(
                self.phone, account.company_id.country_id)
            if sanitized:
                clauses.append(("phone_sanitized", "=", sanitized))
        if not clauses:
            return self.env["crm.lead"]
        window = self.form_id.duplicate_window_days or 30
        cutoff = fields.Datetime.now() - timedelta(days=window)
        domain = ["|"] * (len(clauses) - 1) + clauses
        domain.append(("create_date", ">=", cutoff))
        leads = self.env["crm.lead"].sudo().search(
            domain, order="probability desc, id desc", limit=5)
        active = leads.filtered(lambda l: not l.stage_id.is_won and l.active)
        return (active or leads)[:1]

    # ------------------------------------------------------------- crm values
    def _prepare_crm_lead_vals(self):
        self.ensure_one()
        account = self.account_id
        form = self.form_id
        vals = {
            "type": "lead",
            "name": f"{form.name or account.name}: "
                    f"{self.contact_name or self.email or self.external_id}",
            "contact_name": self.contact_name,
            "email_from": self.email,
            "phone": self.phone,
            "company_id": account.company_id.id,
            "team_id": (form.team_id or account.lead_team_id).id or False,
            "user_id": (form.user_id or account.lead_user_id).id or False,
            "campaign_id": self.campaign_id.utm_campaign_id.id or False,
            "source_id": account._get_utm_source().id or False,
            "medium_id": account._get_utm_medium().id or False,
            "referred": form.name or False,
            "gclid": self.gclid or False,
        }
        description, extra = [], []
        mappings = {m.question_key: m for m in form.mapping_ids} if form else {}
        for entry in self.field_data or []:
            key = (entry.get("key") or "").lower()
            answer = ", ".join(str(v) for v in (entry.get("values") or []) if v)
            label = key
            mapping = mappings.get(key)
            applied = False
            if mapping:
                label = mapping.question_label or key
                applied = self._apply_mapping(vals, mapping, answer)
            if answer and (not applied or not mapping or mapping.fallback_to_description):
                description.append(f"{label}: {answer}")
        if self.is_organic:
            extra.append(_("Source: organic form submission"))
        if self.gclid:
            extra.append(f"gclid: {self.gclid}")
        vals["description"] = "\n".join(description + extra)
        return {key: value for key, value in vals.items() if value not in (None,)}

    def _apply_mapping(self, vals, mapping, answer):
        """Apply one mapped answer; returns True when it landed in a field."""
        field = mapping.crm_field_id
        if not field or not answer:
            return False
        if field.ttype in ("char", "text"):
            if not vals.get(field.name):
                vals[field.name] = answer
            return True
        if field.ttype == "many2one" and field.relation in ("res.country",
                                                           "res.country.state"):
            Model = self.env[field.relation].sudo()
            domain = ["|", ("name", "ilike", answer), ("code", "=ilike", answer)]
            record = Model.search(domain, limit=1)
            if record:
                vals[field.name] = record.id
                return True
        return False

    # ----------------------------------------------------------------- actions
    def action_open_crm_lead(self):
        self.ensure_one()
        lead = self.crm_lead_id or self.duplicate_lead_id
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.lead",
            "res_id": lead.id,
            "view_mode": "form",
        }
