# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class Account_check_return_check(models.TransientModel):
    _name = 'account_check_return_check'
    _description = _('Account_check_return_check')

    check_ids = fields.Many2many(comodel_name="account.check",)
    return_to = fields.Selection(
        string=_('Return To'),
        selection=[
            ('check_box', 'Return Check Box'),
            ('partner', 'Partner'),
        ],required=True
    )
    return_data = fields.Date(
        string=_('Return Date'),
        required=True,
        default=fields.Date.context_today,
    )

    def return_cashed_checks(self):
        for rec in self.check_ids:
            if rec.state == 'cashed':
                move_1 = self.env['account.move'].create({'move_type': 'entry',
                                                        'is_check': True,
                                                        'date': self.return_data,
                                                        'journal_id': rec.journal_id.id,
                                                        'currency_id': rec.currency_id.id,
                                                        'is_return': True,
                                                        # 'checks_ids': [(6, 0, rec.ids)]
                                                        })
                # print('=============', move_1.name)
                # print('=============', rec.ids)
                journal_items = []
                # Transfer from Bank Account to Liquidity transefer account
                journal_items.append({
                    'move_id': move_1.id,
                    'display_type': 'product',
                    'account_id': self.env.company.transfer_account_id.id,
                    'name': 'Returnd '+rec.name+' '+rec.check_no,
                    'amount_currency': rec.amount,
                    'currency_id': rec.currency_id.id,
                    'partner_id': rec.env.company.id
                })
                # print("-------------", rec.currency_id.name)
                journal_items.append({
                    'move_id': move_1.id,
                    'account_id': rec.journal_id.default_account_id.id,
                    'currency_id': rec.currency_id.id,
                    'name': 'Returnd Check ' + rec.check_no,
                    'amount_currency': rec.amount * -1,
                    'partner_id': rec.env.company.id,
                })
                lines = self.env['account.move.line'].create(journal_items)
                # print("---------------", rec.currency_id.id)
                # print("---------------", rec.amount)
                # move.write({
                #     'line_ids': [(6, 0, lines.ids)]
                # })
                move_1.with_context({"return_type":self.return_to}).action_post()

                journal = self.env['account.journal'].search(
                    [('currency_id', '=', rec.currency_id.id), ('is_returned_check_box', '=', True)])
                if len(journal) == 0:
                    raise ValidationError(_("Please Create Journal For Return Checks."))
                move_2 = self.env['account.move'].create({'move_type': 'entry',
                                                        'is_check': True,
                                                        'journal_check_id': rec.journal_id.id,
                                                        'date': self.return_data,
                                                        'journal_id': journal[0].id,
                                                        'currency_id': rec.currency_id.id,
                                                        'is_return': True,
                                                        'checks_ids': [(6, 0, rec.ids)],
                                                        })
                # print('=============', move_2.name)
                # print('=============', rec.ids)
                journal_items.clear()
                # Transfer from Liquidity payments to Return check journal account
                journal_items.append({
                    'move_id': move_2.id,
                    'display_type': 'product',
                    'account_id': journal.return_check_account.id if self.return_to == "check_box" else rec.partner_id.property_account_receivable_id.id,
                    'name': 'Returnd '+rec.name+' '+rec.check_no,
                    'check_id': rec.id,
                    'amount_currency': rec.amount,
                    'currency_id': rec.currency_id.id,
                    'partner_id': rec.env.company.id if self.return_to == "check_box" else rec.partner_id.id
                })
                
                journal_items.append({
                    'move_id': move_2.id,
                    'account_id': self.env.company.transfer_account_id.id,
                    'currency_id': rec.currency_id.id,
                    'name': 'Returnd Check ' + rec.check_no,
                    'amount_currency': rec.amount * -1,
                    'partner_id': rec.env.company.id
                })

                lines = self.env['account.move.line'].create(journal_items)
                
                move_2.with_context({"return_type":self.return_to}).action_post()
                liq_to_reconcile = move_1.line_ids.filtered(lambda line: line.account_id.id == self.env.company.transfer_account_id.id) + move_2.line_ids.filtered(lambda line: line.account_id.id == self.env.company.transfer_account_id.id)
                liq_to_reconcile.action_reconcile()
                # rec.journal_id = journal[0].id
            elif rec.state in ['collected', 'deposited']:
                move_1 = self.env['account.move'].create({'move_type': 'entry',
                                                        'is_check': True,
                                                        'date': self.return_data,
                                                        'journal_id': rec.journal_id.id,
                                                        'currency_id': rec.currency_id.id,
                                                        'is_return': True,
                                                        # 'checks_ids': [(6, 0, rec.ids)]
                                                        })
                # print('=============', move_1.name)
                # print('=============', rec.ids)
                journal_items = []
                # Transfer from Bank Account to Liquidity transefer account
                journal_items.append({
                    'move_id': move_1.id,
                    'display_type': 'product',
                    'account_id': self.env.company.transfer_account_id.id,
                    'name': 'Returnd '+rec.name+' '+rec.check_no,
                    'amount_currency': rec.amount,
                    'currency_id': rec.currency_id.id,
                    'partner_id': rec.env.company.id
                })
                # print("-------------", rec.currency_id.name)
                journal_items.append({
                    'move_id': move_1.id,
                    'account_id': rec.operation_ids[-1].move_id.line_ids.filtered(lambda x: x.check_id.id == rec.id).account_id.id if rec.operation_ids[-1].move_id else rec.operation_ids[-1].payment_id.move_id.line_ids.filtered(lambda x: x.check_id.id == rec.id).account_id.id,
                    'check_id': rec.id,
                    'currency_id': rec.currency_id.id,
                    'name': 'Returnd Check ' + rec.check_no,
                    'amount_currency': rec.amount * -1,
                    'partner_id': rec.env.company.id,
                })
                self.env['account.move.line'].create(journal_items)
                # print("---------------", rec.currency_id.id)
                # print("---------------", rec.amount)
                # move.write({
                #     'line_ids': [(6, 0, lines.ids)]
                # })
                move_1.with_context({"return_type":self.return_to}).action_post()

                journal = self.env['account.journal'].search(
                    [('currency_id', '=', rec.currency_id.id), ('is_returned_check_box', '=', True)])
                if len(journal) == 0:
                    raise ValidationError(_("Please Create Journal For Return Checks."))
                move_2 = self.env['account.move'].create({'move_type': 'entry',
                                                        'is_check': True,
                                                        'journal_check_id': rec.journal_id.id,
                                                        'date': self.return_data,
                                                        'journal_id': journal[0].id,
                                                        'currency_id': rec.currency_id.id,
                                                        'is_return': True,
                                                        'checks_ids': [(6, 0, rec.ids)],
                                                        })
                # print('=============', move_2.name)
                # print('=============', rec.ids)
                journal_items.clear()
                # Transfer from Liquidity payments to Return check journal account
                journal_items.append({
                    'move_id': move_2.id,
                    'display_type': 'product',
                    'account_id': journal.return_check_account.id if self.return_to == "check_box" else rec.partner_id.property_account_receivable_id.id,
                    'name': 'Returnd '+rec.name+' '+rec.check_no,
                    'check_id': rec.id,
                    'amount_currency': rec.amount,
                    'currency_id': rec.currency_id.id,
                    'partner_id': rec.env.company.id if self.return_to == "check_box" else rec.partner_id.id
                })
                
                journal_items.append({
                    'move_id': move_2.id,
                    'account_id': self.env.company.transfer_account_id.id,
                    'currency_id': rec.currency_id.id,
                    'name': 'Returnd Check ' + rec.check_no,
                    'amount_currency': rec.amount * -1,
                    'partner_id': rec.env.company.id
                })

                lines = self.env['account.move.line'].create(journal_items)
                
                move_2.with_context({"return_type":self.return_to}).action_post()
                liq_to_reconcile = move_1.line_ids.filtered(lambda line: line.account_id.id == self.env.company.transfer_account_id.id) + move_2.line_ids.filtered(lambda line: line.account_id.id == self.env.company.transfer_account_id.id)
                liq_to_reconcile.action_reconcile()
                # rec.journal_id = journal[0].id
