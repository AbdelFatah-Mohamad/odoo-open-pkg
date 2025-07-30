# -*- coding: utf-8 -*-
from odoo import models, fields, api

# Try to import state_list, provide fallback if module structure changes
try:
    from odoo.addons.odoo_check_managment.models.account_check import state_list
except ImportError:
    state_list = []

class ChecksIssuedBankWizardHistory(models.Model):
    _name = 'checks.issued.bank.wizard.history'
    _description = 'Checks Issued Bank Wizard History per User'

    user_id = fields.Many2one(
        'res.users', string='User', required=True, index=True, ondelete='cascade')
    from_issue_date = fields.Date(string='From Issue Date')
    to_issue_date = fields.Date(string='To Issue Date')
    from_due_date = fields.Date(string='From Due Date')
    to_due_date = fields.Date(string='To Due Date')
    as_off_date = fields.Date()
    currency_ids = fields.Many2many('res.currency', string='Currencies')
    partner_ids = fields.Many2many('res.partner', string='Partners')
    journal_ids = fields.Many2many('account.journal', relation='history_journal_rel_bank_wizard', string='Journals')
    source_journal_ids = fields.Many2many(
        'account.journal', relation='history_source_journal_rel_bank_wizard', string='Journals Source')
    dest_journal_ids = fields.Many2many(
        'account.journal', relation='history_dest_journal_rel_bank_wizard', string='Journals Destination')
    check_book_ids = fields.Many2many('account.check.book', string='Check Books')
    bank_check_option = fields.Selection(
        lambda self: state_list + [('all', 'All Checks')], # Use lambda
        string='Bank check option'
    )

    _sql_constraints = [
        ('user_id_uniq', 'unique (user_id)', 'History must be unique per user!'),
    ]
