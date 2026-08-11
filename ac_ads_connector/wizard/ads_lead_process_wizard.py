# -*- coding: utf-8 -*-
from odoo import fields, models


class AdsLeadProcessWizard(models.TransientModel):
    _name = "ads.lead.process.wizard"
    _description = "Process Staged Ad Leads"

    lead_ids = fields.Many2many(
        "ads.lead", default=lambda self: self.env.context.get("active_ids"))
    force_reprocess = fields.Boolean(
        help="Also re-run rows in Error or Skipped state.")

    def action_process(self):
        self.ensure_one()
        self.lead_ids.action_process(force=self.force_reprocess)
        processed = len(self.lead_ids.filtered(
            lambda lead: lead.state in ("processed", "merged")))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": f"{processed} of {len(self.lead_ids)} submissions are "
                           f"now linked to CRM leads.",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
