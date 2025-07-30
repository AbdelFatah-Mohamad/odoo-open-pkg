# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def create_return_lines(self,vals):
        if (self.is_check == True) and self.move_type == 'entry' and len(self.checks_ids) > 0 and self.journal_check_id and self.account_check_id:
            lines = []
            total_amount_currency = 0
            debit = 0
            credit = 0
            curruncy = False
            for check in self.checks_ids:
                line = check.payment_id.move_id.line_ids.filtered(lambda x: x.check_id.id == check.id)
                # print('=============== ',check.payment_id.move_id.name)
                # print('=============== ',line.check_id.name)
                if self.line_ids.filtered(lambda x: x.check_id.id == check.id):
                    continue
                lines.append({
                    'account_id': line.account_id.id,
                    'partner_id': line.partner_id.id,
                    'name': line.name,
                    'move_id': self.id,
                    'check_id': line.check_id.id,
                    'currency_rate': line.currency_rate,
                    'amount_currency': -line.amount_currency,
                    'currency_id': line.currency_id.id or check.currency_id.id,
                })
                total_amount_currency += (line.amount_currency)
                curruncy = line.currency_id.id or check.currency_id.id
                curruncy_rate = line.currency_rate
            # print("========== ",acc.journal_id.default_account_id.name)
            # print("========== ",curruncy)
            lines.append({
                'account_id': self.account_check_id.id,
                'name': "Transerfer checks",
                'move_id': self.id,
                'amount_currency': total_amount_currency,
                'currency_rate': line.currency_rate,
                'currency_id': curruncy,
            })
            self.env['account.move.line'].create(lines)
            vals["currency_rate"] = curruncy_rate
        return vals
