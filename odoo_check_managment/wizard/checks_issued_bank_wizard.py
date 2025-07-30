# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.addons.odoo_check_managment.models.account_check import state_list
_logger = logging.getLogger(__name__)


class ChecksIssuedBankWizard(models.TransientModel):
    _name = 'checks.issued.bank.wizard'
    _description = _('ChecksIssuedBankWizard')

    from_issue_date = fields.Date(
        string=_('From Issue Date'),
        help=_('Issue Date of the Check is payment date of the check'),
    )

    to_issue_date = fields.Date(
        string=_('To Issue Date'),
    )
    from_due_date = fields.Date(
        string=_('From Due Date'),
    )

    to_due_date = fields.Date(
        string=_('To Due Date'),
    )

    as_off_date = fields.Date()

    currency_ids = fields.Many2many(
        string=_('Currencies'),
        comodel_name='res.currency',
    )

    partner_ids = fields.Many2many(
        string=_('Partners'),
        comodel_name='res.partner',
    )

    journal_ids = fields.Many2many(
        comodel_name='account.journal', string='Journals')
    source_journal_ids = fields.Many2many(
        comodel_name='account.journal', relation='source_journal_checks_issued_bank_rl',string='Journals Source')
    dest_journal_ids = fields.Many2many(
        comodel_name='account.journal', relation='dest_journal_checks_issued_bank_rl',string='Journals Destination')

    check_book_ids = fields.Many2many(
        comodel_name='account.check.book', string='Check Books')

    # currency_ids = fields.Many2many(
    #     string=_('Currencies'),
    #     comodel_name='res.currency',
    # )

    bank_check_option = fields.Selection(state_list + [('all', 'All Checks')],
        string='Bank check option')

    @api.model
    def default_get(self, fields_list):
        defaults = super(ChecksIssuedBankWizard, self).default_get(fields_list)
        history = self.env['checks.issued.bank.wizard.history'].search([('user_id', '=', self.env.uid)], limit=1)
        if history:
            # Map history fields to defaults, checking if the field is requested
            history_vals = history.read()[0] # Read all fields from history
            for field_name in fields_list:
                # Check if field exists in history and has a value (not False)
                if field_name in history_vals and history_vals[field_name] is not False and field_name != 'id':
                    field_obj = self._fields.get(field_name)
                    # Handle M2M fields specifically (read returns list of IDs)
                    if isinstance(field_obj, fields.Many2many):
                        defaults[field_name] = [(6, 0, history_vals[field_name])]
                    # Handle M2O fields (read returns tuple (id, name))
                    elif isinstance(field_obj, fields.Many2one) and isinstance(history_vals[field_name], tuple):
                         defaults[field_name] = history_vals[field_name][0] # Use only the ID
                    # Handle other field types
                    else:
                        defaults[field_name] = history_vals[field_name]

            # Ensure as_off_date defaults to today if not set in history or not requested initially but needed
            if 'as_off_date' in fields_list and not defaults.get('as_off_date'):
                 defaults['as_off_date'] = fields.Date.context_today(self)
        else:
             # Set default as_off_date if no history exists and it's requested
            if 'as_off_date' in fields_list:
                defaults['as_off_date'] = fields.Date.context_today(self)
        return defaults

    def _save_history(self):
        """Saves the current wizard values to the history model."""
        self.ensure_one()
        history_model = self.env['checks.issued.bank.wizard.history']
        history = history_model.search([('user_id', '=', self.env.uid)], limit=1)
        vals = {
            'user_id': self.env.uid,
            'from_issue_date': self.from_issue_date,
            'to_issue_date': self.to_issue_date,
            'from_due_date': self.from_due_date,
            'to_due_date': self.to_due_date,
            'as_off_date': self.as_off_date,
            'currency_ids': [(6, 0, self.currency_ids.ids)],
            'partner_ids': [(6, 0, self.partner_ids.ids)],
            'journal_ids': [(6, 0, self.journal_ids.ids)],
            'source_journal_ids': [(6, 0, self.source_journal_ids.ids)],
            'dest_journal_ids': [(6, 0, self.dest_journal_ids.ids)],
            'check_book_ids': [(6, 0, self.check_book_ids.ids)],
            'bank_check_option': self.bank_check_option,
        }
        # Remove keys with False values to avoid overwriting existing history with empty values
        vals = {k: v for k, v in vals.items() if v is not False}

        if history:
            history.write(vals)
        else:
            # Ensure user_id is present for creation
            if 'user_id' not in vals:
                vals['user_id'] = self.env.uid
            history_model.create(vals)
    
    def get_lines(self):
        domain = []
        
        if self.from_issue_date:
            domain.append(("create_date", ">=", self.from_issue_date))
        if self.to_issue_date:
            domain.append(("create_date", "<=", self.to_issue_date))
        
        if self.as_off_date:
            domain.append(("create_date", "<=", self.as_off_date))
        
        if self.from_due_date:
            domain.append(("due_date", ">=", self.from_due_date))
        if self.to_due_date:
            domain.append(("due_date", "<=", self.to_due_date))
        
        if len(self.partner_ids):
            domain.append(("partner_id", "in", self.partner_ids.ids))
        
        if len(self.check_book_ids):
            domain.append(("check_book_id", "in", self.check_book_ids.ids))
        
        if len(self.journal_ids):
            domain.append(("journal_id", "in", self.journal_ids.ids))
        
        if len(self.currency_ids):
            domain.append(("currency_id", "in", self.currency_ids.ids))
        checks = self.env["account.check"].search(domain)
        
        line_ids = []
        for check in checks:
            try:
                operation = check.operation_ids.filtered(lambda x: x.account_date).filtered(lambda x: x.account_date <= self.as_off_date)
                if len(operation) == 0:
                    continue
                
                if len(self.source_journal_ids):
                    if not operation[-1].journal_id.id in self.source_journal_ids.ids:
                        continue
                if len(self.dest_journal_ids):
                    if not operation[-1].destination_journal_id.id in self.dest_journal_ids.ids:
                        continue
                # if self.bank_check_option:
                #     operation = operation.filtered_domain([("state", "=", self.bank_check_option )]).sorted(key=lambda x: x.create_date)
                if self.bank_check_option == 'all':
                    line = operation[-1].move_id.line_ids.filtered(lambda x: x.check_id.id == check.id).id if len(operation) > 0 else False
                    if line:
                        line_ids.append(line)
                elif operation[-1].state == self.bank_check_option:
                    line = operation[-1].move_id.line_ids.filtered(lambda x: x.check_id.id == check.id).id if len(operation) > 0 else False
                    if line:
                        line_ids.append(line)
            except Exception as e:
                _logger.error("Error in get_lines: %s", e)
        self._save_history()
        return line_ids
    
    def lines(self):
        lines = self.env["account.move.line"].browse(self.get_lines())
        lines.assign_check_state_in_operation()
        return lines
    
    def view(self):
        action = self.sudo().env.ref('account.action_account_moves_all').read()[0]
        action['domain'] = [('id', 'in', self.lines().ids)]
        return action

    def print(self):
        return self.env.ref('odoo_check_managment.report_checks_issued_bank_account_move_line').report_action(self)