# -*- coding: utf-8 -*-
from odoo import api, fields, models

#: well-known platform question keys -> crm.lead field (both providers feed
#: lowercased keys through the normalized row shape).
DEFAULT_QUESTION_MAP = {
    "email": "email_from",
    "work_email": "email_from",
    "full_name": "contact_name",
    "first_name": "contact_name",
    "phone_number": "phone",
    "phone": "phone",
    "work_phone_number": "phone",
    "company_name": "partner_name",
    "job_title": "function",
    "city": "city",
    "street_address": "street",
    "zip_code": "zip",
    "post_code": "zip",
    "postal_code": "zip",
    "country": "country_id",
    "state": "state_id",
    "region": "state_id",
    "province": "state_id",
}


class AdsLeadForm(models.Model):
    _name = "ads.lead.form"
    _description = "Ad Lead Form"
    _order = "id desc"

    name = fields.Char(required=True)
    account_id = fields.Many2one(
        "ads.account", required=True, ondelete="cascade", index=True)
    provider = fields.Selection(related="account_id.provider", store=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    external_id = fields.Char(index=True, copy=False, readonly=True)
    status = fields.Char(readonly=True)
    page_external_id = fields.Char(readonly=True)
    locale = fields.Char(readonly=True)
    questions = fields.Json(readonly=True)
    active = fields.Boolean(default=True)

    # per-form overrides; empty -> account defaults apply
    create_crm_lead = fields.Boolean(
        default=True, help="Turn this form's staged leads into CRM leads automatically "
                           "(the account-level toggle must also be on).")
    team_id = fields.Many2one("crm.team", string="Sales Team")
    user_id = fields.Many2one("res.users", string="Salesperson")
    duplicate_window_days = fields.Integer(
        default=30, help="How far back duplicate detection looks for a matching lead.")

    leads_synced_until = fields.Datetime(copy=False, readonly=True)
    mapping_ids = fields.One2many("ads.lead.field.map", "form_id")
    lead_ids = fields.One2many("ads.lead", "form_id")
    leads_count = fields.Integer(compute="_compute_leads_count")

    _external_unique = models.UniqueIndex(
        "(external_id, account_id) WHERE external_id IS NOT NULL")

    @api.depends("lead_ids")
    def _compute_leads_count(self):
        for form in self:
            form.leads_count = len(form.lead_ids)

    # ------------------------------------------------------------------- sync
    @api.model
    def _upsert_from_row(self, account, row):
        form = self.sudo().with_context(active_test=False).search([
            ("account_id", "=", account.id),
            ("external_id", "=", row["external_id"]),
        ], limit=1)
        vals = {
            "name": row.get("name") or f"Form {row['external_id']}",
            "status": row.get("status"),
            "page_external_id": row.get("page_external_id"),
            "locale": row.get("locale"),
            "questions": row.get("questions"),
        }
        if form:
            form.write(vals)
        else:
            form = self.sudo().create([dict(
                vals, account_id=account.id, external_id=row["external_id"])])
            form._generate_default_mappings()
        return form

    def _generate_default_mappings(self):
        """Seed mapping lines from the platform's question list using the
        well-known key table. Idempotent — existing keys are left alone."""
        IrModelFields = self.env["ir.model.fields"].sudo()
        for form in self:
            known = set(form.mapping_ids.mapped("question_key"))
            questions = form.questions or []
            keys = []
            for question in questions:
                if isinstance(question, dict):
                    key = (question.get("key") or question.get("type") or "").lower()
                    label = question.get("label") or question.get("question_text") or key
                else:
                    key, label = str(question).lower(), str(question)
                if key and key not in known:
                    keys.append((key, label))
            lines = []
            for sequence, (key, label) in enumerate(keys, start=1):
                field_name = DEFAULT_QUESTION_MAP.get(key)
                field = IrModelFields.search([
                    ("model", "=", "crm.lead"), ("name", "=", field_name),
                    ("store", "=", True)], limit=1) if field_name else IrModelFields
                lines.append({
                    "form_id": form.id,
                    "sequence": sequence,
                    "question_key": key,
                    "question_label": label,
                    "crm_field_id": field.id or False,
                })
            if lines:
                self.env["ads.lead.field.map"].sudo().create(lines)

    def action_sync_leads_now(self):
        self.ensure_one()
        return self.env["ads.sync.engine"]._sync_leads_now(self.account_id)


class AdsLeadFieldMap(models.Model):
    _name = "ads.lead.field.map"
    _description = "Lead Form Field Mapping"
    _order = "sequence, id"

    form_id = fields.Many2one(
        "ads.lead.form", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    question_key = fields.Char(required=True,
                               help="Platform field name (e.g. email, full_name).")
    question_label = fields.Char()
    crm_field_id = fields.Many2one(
        "ir.model.fields", ondelete="cascade",
        domain="[('model', '=', 'crm.lead'), ('store', '=', True), "
               "('readonly', '=', False), "
               "('ttype', 'in', ('char', 'text', 'many2one'))]",
        help="Empty: the answer only lands in the lead description.")
    fallback_to_description = fields.Boolean(
        default=True, help="Also include this answer in the description dump.")

    _map_unique = models.UniqueIndex("(form_id, question_key)")
