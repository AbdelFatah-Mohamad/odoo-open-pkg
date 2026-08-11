# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class AdsAudienceUploadWizard(models.TransientModel):
    _name = "ads.audience.upload.wizard"
    _description = "Upload Audience Members"

    audience_id = fields.Many2one("ads.audience", required=True, ondelete="cascade")
    partner_ids = fields.Many2many(
        "res.partner", string="Contacts",
        domain="[('email', '!=', False)]",
        help="Contacts whose email/phone will be normalized, SHA-256 hashed at "
             "push time and matched by the platform. Hashes are never stored.")

    def action_upload(self):
        self.ensure_one()
        audience = self.audience_id
        if not audience.external_id:
            raise UserError(_("Create the audience on the platform first."))
        if not self.partner_ids:
            raise UserError(_("Pick at least one contact."))
        job = self.env["ads.push.job"]._enqueue(
            audience, "audience_users", {"partner_ids": self.partner_ids.ids})
        job._run_one()
        job_state = job.state
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if job_state == "done" else "warning",
                "message": _("%(count)s contacts queued for %(name)s (%(state)s).",
                             count=len(self.partner_ids), name=audience.name,
                             state=job_state),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
