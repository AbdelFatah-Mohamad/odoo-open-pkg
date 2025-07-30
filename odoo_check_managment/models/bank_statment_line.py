from odoo import models, fields, api, _, Command


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'
    _description = _('Account Bank Statement Line')

    check_id  = fields.Many2one('account.check', string='Check')
    
    def action_undo_reconciliation(self):
        """ Undo the reconciliation made on the statement line and reset their journal items
        to their original states.
        """
        for line in self.line_ids:
            if line.check_id:
                line.check_id.state = "deposited"
                self.env["account.check.operation"].create({
                    'check_id': line.check_id.id,
                    'action_date': fields.Datetime.now(),
                    'state': line.check_id.state,
                    'journal_id': self.journal_id.id,
                    'move_id': self.move_id.id,
                    'partner_id': line.check_id.partner_id.id,
                    # 'destination_journal_id': self.journal_id.id,
                    'operation_type': "reset_reconcile"
                })
        self.line_ids.remove_move_reconcile()
        self.payment_ids.unlink()

        for st_line in self:
            st_line.with_context(force_delete=True, skip_readonly_check=True).write({
                'checked': True,
                'line_ids': [Command.clear()] + [
                    Command.create(line_vals) for line_vals in st_line._prepare_move_line_default_vals()],
            })
