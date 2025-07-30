from odoo import models, fields, api, _
from .account_check import state_list 

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'
    _description = _('Account Move Line')
    
    check_id = fields.Many2one(comodel_name="account.check")
    x_studio_chk_due_date = fields.Date(related='check_id.due_date', string="Ch Due Date", store=True)
    check_state = fields.Selection(related='check_id.state', string="Check State", store=True)
    operation_check_state = fields.Selection(
        selection=state_list,readonly=True, string="CH OP State"
    )
    source_journal_id = fields.Many2one(comodel_name="account.journal", string="Check Journal")
    destination_journal_id = fields.Many2one(comodel_name="account.journal", string="Destination Journal")

    operation_time = fields.Date(string="CH OP Date", readonly=True)

    def assign_check_state_in_operation(self):
        for rec in self.filtered(lambda x: x.check_id):
            operations = rec.check_id.operation_ids.filtered(lambda x: x.move_id.id == rec.move_id.id)
            operation_check_state = operations[-1].state if len(operations) > 0 else False
            operation_time = operations[-1].account_date if len(operations) > 0 else False
            destination_journal_id = operations[-1].destination_journal_id if len(operations) > 0 else False
            source_journal_id = operations[-1].journal_id.id if len(operations) > 0 else False
            if operation_check_state:
                rec.operation_check_state = operation_check_state
                rec.operation_time = operation_time
                rec.source_journal_id = source_journal_id
                rec.destination_journal_id = destination_journal_id.id if destination_journal_id else False
            else:
                move = rec.move_id.origin_payment_id.paired_internal_transfer_payment_id.move_id
                operations = rec.check_id.operation_ids.filtered(lambda x: x.move_id.id == move.id)
                rec.operation_check_state = operations[-1].state if len(operations) > 0 else False
                rec.operation_time = operations[-1].account_date if len(operations) > 0 else False
                rec.destination_journal_id = operations[-1].destination_journal_id.id if len(operations) > 0 else False
                rec.source_journal_id = operations[-1].journal_id.id if len(operations) > 0 else False