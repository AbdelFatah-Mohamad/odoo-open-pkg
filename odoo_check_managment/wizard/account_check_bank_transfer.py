# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AccountCheckBankTransfer(models.TransientModel):
    _name = 'account.check.bank.transfer'
    _description = _('AccountCheckBankTransfer')

    def default_get_c(self):
        checks = self.env['account.check']  # Initialize an empty recordset
        if 'default_check_ids' in self._context:
            checks = checks.browse(self._context["default_check_ids"])
            if checks and len(set(checks.mapped("currency_id").ids)) > 1:
                raise ValidationError("All Checks Must Be Same Currency.")
        if checks:
            return checks[0].currency_id
        return self.env.company.currency_id  # Default to company currency

    transfer_type = fields.Selection(selection=[
        ('check_box', 'Check Box'),
        ('bank', 'Bank')
    ], string='Transfer Type')
    check_ids = fields.Many2many("account.check")
    journal_bank_id = fields.Many2one("account.journal")
    currency_id = fields.Many2one("res.currency",default=default_get_c)
    transfer_date = fields.Date(
        string=_('Transfer Date'),
        required=True,
        default=fields.Date.context_today,
    )
    def transfer(self):
        if len(self.check_ids.payment_id.filtered(lambda x :x.state not in ("in_process", "paid"))) > 0:
            raise ValidationError(_("Please Confirm Payments First."))
        
        if self.transfer_type == "bank":
            grouped_checks = self.check_ids.filtered_domain([('state', '=', 'collected')]).grouped("journal_id")
            for journal, checks in grouped_checks.items():
                name_of_liq = f'Internal Transfer {journal.currency_id.symbol}{sum(checks.mapped("amount"))} - {self.env.company.name} - {fields.Date.today()}'
                move_1 = self.env['account.move'].create({'move_type': 'entry',
                                                                # 'is_check': True,
                                                                'date': self.transfer_date,
                                                                # 'journal_check_id': rec.journal_id.id,
                                                                'journal_id': journal.id,
                                                                'currency_id': journal.currency_id.id,
                                                                })
                journal_items = []
                # Transfer from Checkbox account to Liquidity transefer account
                for check in checks:
                    journal_items.append({
                        'move_id': move_1.id,
                        'display_type': 'product',
                        'account_id': check.journal_id.default_account_id.id,
                        'name': 'Transfer '+check.name+' '+check.check_no,
                        'check_id': check.id,
                        'amount_currency': check.amount * -1,
                        'currency_id': check.currency_id.id,
                        'partner_id': self.env.company.id
                    })
                journal_items.append({
                    'move_id': move_1.id,
                    'account_id': self.env.company.transfer_account_id.id,
                    'currency_id': checks[0].currency_id.id,
                    'name': name_of_liq,
                    'amount_currency': sum(checks.mapped("amount")),
                    'partner_id': self.env.company.id,
                    'check_id': False,
                    # 'credit': total,
                    # 'partner_id': partner_id
                })
                lines = self.env['account.move.line'].create(journal_items)
                move_1.write({
                    'line_ids': [(6, 0, lines.ids)]
                })
                move_1.with_context({'is_transfer':True}).action_post()

                move_2 = self.env['account.move'].create({'move_type': 'entry',
                                                        'date': self.transfer_date,
                                                        'journal_id': self.journal_bank_id.id,
                                                        'currency_id': self.journal_bank_id.currency_id.id,
                                                        })
                journal_items.clear()
                # Transfer from Liquidity payments to Return check journal account
                journal_items.append({
                    'move_id': move_2.id,
                    'account_id': self.env.company.transfer_account_id.id,
                    'currency_id': self.journal_bank_id.currency_id.id,
                    'name': name_of_liq,
                    'amount_currency': sum(checks.mapped("amount")) * -1,
                    'check_id': False,
                    'partner_id': self.env.company.id,
                })
                bank_receipt_account = self.journal_bank_id.inbound_payment_method_line_ids[0].payment_account_id
                if bank_receipt_account == False:
                    raise ValidationError(_(f"Please Set Outstanding Receipts accounts into {self.journal_bank_id.name}."))
                for check in checks:
                    journal_items.append({
                        'move_id': move_2.id,
                        'display_type': 'product',
                        'account_id': bank_receipt_account.id,
                        # 'account_id': rec.partner_id.property_account_receivable_id.id,
                        'name': 'Transfer '+check.name+' '+check.check_no,
                        'check_id': check.id,
                        'amount_currency': check.amount,
                        'currency_id': check.currency_id.id,
                        'partner_id': self.env.company.id
                    })
                
               
                lines = self.env['account.move.line'].create(journal_items)
                # print("---------------", sum([line.debit for line in lines]))
                # print("---------------", sum([line.credit for line in lines]))
                # print("---------------", rec.amount)
                move_2.write({
                    'line_ids': [(6, 0, lines.ids)]
                })
                move_2.with_context({'is_transfer':True, 'transfer_type':self.transfer_type}).action_post()
                liq_to_reconcile = move_1.line_ids.filtered(lambda line: line.account_id.id == self.env.company.transfer_account_id.id) + move_2.line_ids.filtered(lambda line: line.account_id.id == self.env.company.transfer_account_id.id)
                liq_to_reconcile.action_reconcile()
        else:
            grouped_checks = self.check_ids.filtered_domain([('state', '=', 'returned')]).grouped("journal_id")
            # Transfer From returned checkbox to checkbox
            for journal, checks in grouped_checks.items():
                name_of_liq = f'Internal Transfer (CheckBox) {journal.currency_id.symbol}{sum(checks.mapped("amount"))}'
                move_1 = self.env['account.move'].create({'move_type': 'entry',
                                                                'date': self.transfer_date,
                                                                # 'is_check': True,
                                                                # 'journal_check_id': rec.journal_id.id,
                                                                'journal_id': self.journal_bank_id.id,
                                                                'currency_id': journal.currency_id.id,
                                                                })
                journal_items = []
                # Transfer from Checkbox account to Liquidity transefer account
                for check in checks:
                    journal_items.append({
                        'move_id': move_1.id,
                        'display_type': 'product',
                        'account_id': check.journal_id.return_check_account.id,
                        'name': 'Transfer Returned Check '+check.name+' '+check.check_no,
                        'amount_currency': check.amount * -1,
                        'currency_id': check.currency_id.id,
                        'partner_id': self.env.company.id,
                        'check_id': check.id,
                    })
                    journal_items.append({
                        'move_id': move_1.id,
                        'account_id': self.journal_bank_id.default_account_id.id,
                        'currency_id': check.currency_id.id,
                        'name': name_of_liq,
                        'amount_currency': check.amount ,
                        'partner_id': self.env.company.id,
                        'check_id': check.id,
                        # 'credit': total,
                        # 'partner_id': partner_id
                    })
                lines = self.env['account.move.line'].create(journal_items)
                move_1.write({
                    'line_ids': [(6, 0, lines.ids)]
                })
                move_1.with_context({'is_transfer':True, 'transfer_type':self.transfer_type}).action_post()
