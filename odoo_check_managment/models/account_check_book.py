# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import date
from dateutil import relativedelta
_logger = logging.getLogger(__name__)


class AccountCheckBook(models.Model):
    _name = 'account.check.book'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'analytic.mixin']
    _description = 'AccountCheckBook'

    name = fields.Char('Name')
    account_number = fields.Char()
    state = fields.Selection(
        string=_('State'),
        selection=[
            ('draft', 'Draft'),
            ('generated', 'Generated'),
            ('in_use', 'In Use'),
            ('finished', 'Finished'),
        ],
        default="draft",
        compute="_compute_state",
        store=True
    )    
    @api.depends("check_ids.state")
    def _compute_state(self):
        for record in self:
            # print("^^^^^^^^^^^^^^ ", record.state)
            # print("^^^^^^^^^^^^^^ ", len(record.check_ids.mapped("state")))
            # print("^^^^^^^^^^^^^^ ", len(record.check_ids))
            if len(record.check_ids) == 0:
                record.state = "draft"
            elif len(record.check_ids.filtered(lambda x: x.payment_id.id != False or x.state == 'cancelled')) == len(record.check_ids):
                record.state = "finished"
            elif len(record.check_ids.filtered(lambda x: x.state == "check_book")) == len(record.check_ids):
                record.state = "generated"
            else:
                record.state = "in_use"
    check_ids = fields.One2many(
        string=_('Check_ids'),
        comodel_name='account.check',
        inverse_name='check_book_id',
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency", related='journal_id.currency_id')
    bank_id = fields.Many2one(
        comodel_name="res.bank", string="Check Bank", required=True)
    journal_id = fields.Many2one(
        comodel_name="account.journal", required=True)
    
    check_no_from = fields.Integer(string="Start From")
    num_of_check = fields.Integer(string="Number of Checks")
    
    is_cancelled_check = fields.Boolean(
        string='is_cancelled_check',
        compute='_compute_is_cancelled_check',
    )
    
    @api.depends("check_ids.state")
    def _compute_is_cancelled_check(self):
        for record in self:
            record.is_cancelled_check = True if len(record.check_ids.filtered(lambda x: x.state == "cancelled")) > 0 else False

    def generate_checks(self):
        checks = []
        # date_check = date.today()
        for check_no in range(0, self.num_of_check):
            checks.append((0,0,{
                "check_no": str(self.check_no_from+check_no),
                "journal_id": self.journal_id.id,
                "bank_id": self.bank_id.id,
                "check_type": "out",
                "state": "check_book",
                # "due_date": date_check,
            }))
            # date_check += relativedelta.relativedelta(months=1)
        self.write({
            "check_ids": checks,
            "state": "generated",
        })
    
    @api.model
    def create(self, vals_list):
        vals_list['name'] = self.env["ir.sequence"].next_by_code("account.check.book")
        return super(AccountCheckBook,self).create(vals_list)

