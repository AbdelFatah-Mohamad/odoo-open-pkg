from odoo import models, fields, api, _


class BankRecWidgetLine(models.Model):
    _inherit = 'bank.rec.widget.line'
    _description = _('Bank Rec Widget Line')
    
    check_id = fields.Many2one(comodel_name="account.check")

    @api.depends('flag')
    def _compute_source_aml_fields(self):
        for line in self:
            line.source_aml_move_id = None
            line.source_aml_move_name = None
            if line.flag in ('new_aml', 'liquidity'):
                line.source_aml_move_id = line.source_aml_id.move_id
                line.source_aml_move_name = line.source_aml_id.move_id.name
                if line.source_aml_id.check_id:
                    line.check_id = line.source_aml_id.check_id
            elif line.flag == 'aml':
                partials = line.source_aml_id.matched_debit_ids + line.source_aml_id.matched_credit_ids
                all_counterpart_lines = partials.debit_move_id + partials.credit_move_id
                counterpart_lines = all_counterpart_lines - line.source_aml_id - partials.exchange_move_id.line_ids
                if len(counterpart_lines) == 1:
                    line.source_aml_move_id = counterpart_lines.move_id
                    line.source_aml_move_name = counterpart_lines.move_id.name
