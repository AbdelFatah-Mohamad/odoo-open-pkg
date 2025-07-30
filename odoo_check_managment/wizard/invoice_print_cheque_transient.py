# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################

import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class InvoicePrintBankCheckWizard(models.TransientModel):
    _name = 'invoice.print.res.bank.wizard'
    _description = "Invoice Print Bank Check Wizard"

    @api.model
    def _get_check_id(self):
        check_id = False
        if self._context.get("active_id"):
            if self._context.get("active_model") == "account.check":
                check_id = self._context.get("active_id")
        return check_id

    @api.model
    def _get_amount_in_words(self):
        amount_total_words = ""
        if self._context.get("active_id"):
            if self._context.get("active_model") == "account.check":
                active_obj = self.env["account.check"].browse(self._context.get("active_id"))
                amount_total_words = active_obj.currency_id.amount_to_text(active_obj.amount)
        return amount_total_words

    name = fields.Char(related="partner_id.name", string="Name")
    check_id = fields.Many2one('account.check',default=_get_check_id)
    partner_id = fields.Many2one(related='check_id.partner_id')
    pay_name_line1 = fields.Char("Pay To",related='check_id.partner_id.name')
    pay_name_line2 = fields.Char("Pay To Line2")
    currency_id = fields.Many2one("res.currency", related='check_id.currency_id')
    amount = fields.Monetary("Amount", related='check_id.amount', digits='Amount')
    amount_in_words = fields.Char("Amount In Words",
                                  default=_get_amount_in_words)
    amount_in_words_line2 = fields.Char("Amount In Words Line 2", )
    date = fields.Date("Date On Check", related='check_id.due_date')
    check_book_id = fields.Many2one(related='check_id.check_book_id', string="Check Book")
    check_has_pay_line2 = fields.Boolean(compute="_check_check_attributes")
    check_has_amount_line2 = fields.Boolean(compute="_check_check_attributes")
    is_preview = fields.Boolean("Preview")

    @api.depends("check_book_id")
    def _check_check_attributes(self):
        self.ensure_one()
        self.check_has_pay_line2 = False
        self.check_has_amount_line2 = False
        if self.check_book_id:
            for res_bank_attr in self.check_book_id.bank_id.check_attribute_line_ids.filtered(
                    lambda o: o.name.attribute in ['pay_line2', 'amount_line_2']):
                if res_bank_attr.name.attribute == "pay_line2":
                    self.check_has_pay_line2 = True
                if res_bank_attr.name.attribute == "amount_line_2":
                    self.check_has_amount_line2 = True

    @api.onchange("amount")
    def onchange_amount(self):
        if self.currency_id:
            self.amount_in_words_line2 = False
            self.amount_in_words = self.currency_id.amount_to_text(self.amount)
            self.set_amount_lines_in_word()

    @api.onchange("partner_id")
    def onchange_partner_id(self):
        if self.partner_id:
            self.pay_name_line1 = self.partner_id.name


    def set_amount_lines_in_word(self):
        self.ensure_one()
        if self.amount_in_words:
            if self.check_book_id.bank_id.max_char_in_line1:
                # char_count = len(self.amount_in_words)
                raw_str = self.amount_in_words
                # max_char = self.check_book_id.max_char_in_line1
                line1 = ""
                line2 = ""
                total_word = 0
                for word in raw_str.split(" "):
                    if total_word + len(word) <= self.check_book_id.bank_id.max_char_in_line1:
                        total_word += len(word) + 1
                        line1 += word
                        line1 += " "
                    else:
                        line2 = raw_str[total_word:]
                        break
                self.amount_in_words = line1
                self.amount_in_words_line2 = line2

    def print_check_preview(self):
        self.ensure_one()
        self.is_preview = True
        return self.env.ref(
            'odoo_check_managment.res_bank_leaf_print_report'
        ).report_action(self)

    def print_check(self):
        self.ensure_one()
        self.is_preview = False
        return self.env.ref(
            'odoo_check_managment.res_bank_leaf_print_report'
        ).report_action(self)
