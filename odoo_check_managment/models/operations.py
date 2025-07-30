# -*- coding: utf-8 -*-
import logging

from odoo import models, fields


class AccountCheckOperation(models.Model):
    _name = 'account.check.operation'

    check_id = fields.Many2one(comodel_name="account.check", invisible=True)
    action_date = fields.Datetime(
        string="Time")
    state = fields.Selection(selection=[
                                        ('draft', 'Draft'),
                                        ('check_book', 'Check Box'),
                                        ('collected', 'Collected (CheckBox)'),
                                        ('deposited', 'Deposited'),
                                        ('cashed', 'Cashed'),
                                        ('returned', 'Returned'),
                                        ('returned_to_partner', 'Returned To Partner'),
                                        ('endorsed', 'Endorsed')], string="State")
    payment_state = fields.Selection(selection=[('draft', 'Draft'),
                                                ('in_process', 'In Process'),
                                                ('paid', 'Paid'),
                                                ('canceled', 'Cancelled'),
                                                ('bank_statment','Bank Statment')], string='Payment State')
    operation_type = fields.Selection(selection=[('internal', 'Internal Transfer'),
                                                ('reset_reconcile','Reset Reconcile'),
                                                ('reconcile','Reconcile'),
                                                ('cancel','Cancel'),
                                                ('payment', 'Payment')], string="Operation")
    payment_id = fields.Many2one(
        comodel_name="account.payment", string="Payment")
    journal_id = fields.Many2one(
        comodel_name="account.journal", string="Journal")
    destination_journal_id = fields.Many2one(
        comodel_name="account.journal", string="Destination Journal")
    move_id = fields.Many2one(
        comodel_name="account.move", string="Journal Entry")
    partner_id = fields.Many2one(comodel_name="res.partner", string="Partner")
    user_id = fields.Many2one(comodel_name="res.users", string="User",default = lambda self:self.env.user.id)
    account_date = fields.Date(related='move_id.date', string="Account Date", store=True)
    # note = fields.Text()
