# -*- coding: utf-8 -*-
from markupsafe import Markup, escape

from odoo import api, fields, models


class AdsPushWizard(models.TransientModel):
    """Pre-push review: local value vs last pushed vs remote snapshot for every
    pushable field, so an admin sees exactly what will hit the platform."""

    _name = "ads.push.wizard"
    _description = "Push Changes to Ad Platform"

    res_model = fields.Char(default=lambda self: self.env.context.get("active_model"))
    res_id = fields.Integer(default=lambda self: self.env.context.get("active_id"))
    diff_preview = fields.Html(compute="_compute_diff_preview", sanitize=False)

    def _record(self):
        self.ensure_one()
        if self.res_model in ("ads.campaign", "ads.adset", "ads.ad") and self.res_id:
            return self.env[self.res_model].browse(self.res_id)
        return None

    @api.depends("res_model", "res_id")
    def _compute_diff_preview(self):
        for wizard in self:
            record = wizard._record()
            if not record:
                wizard.diff_preview = ""
                continue
            pushed = record.last_pushed_snapshot or {}
            remote = record.remote_snapshot or {}
            rows = []
            for key in sorted(record._ADS_PUSHABLE_FIELDS):
                local_value = record[key]
                if isinstance(local_value, models.BaseModel):
                    continue
                row = (escape(record._fields[key].string or key),
                       escape(str(local_value or "—")),
                       escape(str(pushed.get(key, "—") or "—")),
                       escape(str(remote.get(key, "—") or "—")))
                highlight = " class='table-warning'" if row[1] != row[2] else ""
                rows.append(
                    f"<tr{highlight}><td>{row[0]}</td><td><b>{row[1]}</b></td>"
                    f"<td>{row[2]}</td><td>{row[3]}</td></tr>")
            wizard.diff_preview = Markup(
                "<table class='table table-sm'><thead><tr><th>Field</th>"
                "<th>Local (will be pushed)</th><th>Last pushed</th>"
                "<th>Remote snapshot</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table>")

    def action_confirm_push(self):
        self.ensure_one()
        record = self._record()
        if record:
            record.action_push()
        return {"type": "ir.actions.act_window_close"}
