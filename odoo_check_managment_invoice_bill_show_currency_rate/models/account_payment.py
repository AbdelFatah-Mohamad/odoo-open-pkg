# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _, Command
from odoo.exceptions import UserError, ValidationError
from math import fsum
_logger = logging.getLogger(__name__)


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    @api.model
    def _get_trigger_fields_to_synchronize(self):
        res = super()._get_trigger_fields_to_synchronize()
        return res+('currency_rate',)


    def _prepare_move_line_default_vals(self, write_off_line_vals=None, force_balance=None):
        ''' Prepare the dictionary to create the default account.move.lines for the current payment.
        :param write_off_line_vals: Optional list of dictionaries to create a write-off account.move.line easily containing:
            * amount:       The amount to be added to the counterpart amount.
            * name:         The label to set on the line.
            * account_id:   The account on which create the write-off.
        :return: A list of python dictionary to be passed to the account.move.line's 'create' method.
        '''
        # super()._prepare_move_line_default_vals()
        self.ensure_one()
        write_off_line_vals = write_off_line_vals or {}

        if not self.outstanding_account_id:
            raise UserError(_(
                "You can't create a new payment without an outstanding payments/receipts account set either on the company or the %s payment method in the %s journal.",
                self.payment_method_line_id.name, self.journal_id.display_name))

        # Compute amounts.
        write_off_line_vals_list = write_off_line_vals or []
        write_off_amount_currency = fsum(
            x['amount_currency'] for x in write_off_line_vals_list)
        write_off_balance = fsum(x['balance']
                                 for x in write_off_line_vals_list)

        if self.payment_type == 'inbound':
            # Receive money.
            liquidity_amount_currency = self.amount
        elif self.payment_type == 'outbound':
            # Send money.
            liquidity_amount_currency = -self.amount
        else:
            liquidity_amount_currency = 0.0

        if not write_off_line_vals and force_balance is not None:
            sign = 1 if liquidity_amount_currency > 0 else -1
            liquidity_balance = sign * abs(force_balance)

        liquidity_balance = liquidity_amount_currency * self.currency_rate
        counterpart_amount_currency = -liquidity_amount_currency - write_off_amount_currency
        counterpart_balance = -liquidity_balance - write_off_balance
        currency_id = self.currency_id.id
        counterpart_line_name = ''.join(
            x[1] for x in self._get_aml_default_display_name_list())

        checks = self._get_check_lines()
        # print(f"______________________ checks {checks}")
        if checks:
            line_vals_list = []
            sum_liquidity_balance = 0
            # Liquidity lines.
            for check in checks:
                if self.payment_type == 'inbound':
                    # Receive money.
                    liquidity_amount_currency = check.amount
                elif self.payment_type == 'outbound':
                    # Send money.
                    liquidity_amount_currency = -check.amount
                else:
                    liquidity_amount_currency = 0.0
                liquidity_balance = liquidity_amount_currency * self.currency_rate
                liquidity_line_name = self._get_liquidity_check_display_name(
                    check)
                line_vals_list.append({
                    'name': liquidity_line_name,
                    'date_maturity': self.date,
                    'amount_currency': liquidity_amount_currency,
                    'currency_id': currency_id,
                    'debit': liquidity_balance if liquidity_balance > 0.0 else 0.0,
                    'credit': -liquidity_balance if liquidity_balance < 0.0 else 0.0,
                    'check_id': check.id,
                    'partner_id': self.partner_id.id,
                    'account_id': self.outstanding_account_id.id,
                })
                sum_liquidity_balance += -liquidity_balance
            # Receivable / Payable.
            counterpart_line_name = ''.join(
                x[1] for x in self._get_aml_default_display_name_list())
            line_vals_list.append(
                {
                    'name': counterpart_line_name,
                    'date_maturity': self.date,
                    'amount_currency': counterpart_amount_currency,
                    'currency_id': currency_id,
                    'debit': sum_liquidity_balance if sum_liquidity_balance > 0.0 else 0.0,
                    'credit': -sum_liquidity_balance if sum_liquidity_balance < 0.0 else 0.0,
                    'partner_id': self.partner_id.id,
                    'account_id': self.destination_account_id.id,
                }
            )
            return line_vals_list + write_off_line_vals_list
        else:
            # Compute a default label to set on the journal items.
            liquidity_line_name = ''.join(
                x[1] for x in self._get_aml_default_display_name_list())

            line_vals_list = [
                # Liquidity line.
                {
                    'name': liquidity_line_name,
                    'date_maturity': self.date,
                    'amount_currency': liquidity_amount_currency,
                    'currency_id': currency_id,
                    'debit': liquidity_balance if liquidity_balance > 0.0 else 0.0,
                    'credit': -liquidity_balance if liquidity_balance < 0.0 else 0.0,
                    'partner_id': self.partner_id.id,
                    'account_id': self.outstanding_account_id.id,
                },
                # Receivable / Payable.
                {
                    'name': counterpart_line_name,
                    'date_maturity': self.date,
                    'amount_currency': counterpart_amount_currency,
                    'currency_id': currency_id,
                    'debit': counterpart_balance if counterpart_balance > 0.0 else 0.0,
                    'credit': -counterpart_balance if counterpart_balance < 0.0 else 0.0,
                    'partner_id': self.partner_id.id,
                    'account_id': self.destination_account_id.id,
                },
            ]
            return line_vals_list + write_off_line_vals_list

    def _synchronize_to_moves(self, changed_fields):
        ''' Update the account.move regarding the modified account.payment.
        :param changed_fields: A list containing all modified fields on account.payment.
        '''
        if self._context.get('skip_account_move_synchronization'):
            return

        if not any(field_name in changed_fields for field_name in self._get_trigger_fields_to_synchronize()):
            return

        # print(
        #     f"---------------------- {self._get_trigger_fields_to_synchronize()}")

        for pay in self.with_context(skip_account_move_synchronization=True):
            liquidity_lines, counterpart_lines, writeoff_lines = pay._seek_for_lines()

            # Make sure to preserve the write-off amount.
            # This allows to create a new payment with custom 'line_ids'.
            # print(f"******* {liquidity_lines}")
            write_off_line_vals = []
            if liquidity_lines and counterpart_lines and writeoff_lines:
                write_off_line_vals.append({
                    'name': writeoff_lines[0].name,
                    'account_id': writeoff_lines[0].account_id.id,
                    'partner_id': writeoff_lines[0].partner_id.id,
                    'currency_id': writeoff_lines[0].currency_id.id,
                    'currency_rate' : 1/self.currency_rate,
                    'amount_currency': sum(writeoff_lines.mapped('amount_currency')),
                    'balance': sum(writeoff_lines.mapped('balance')),
                })

            line_vals_list = pay._prepare_move_line_default_vals(
                write_off_line_vals=write_off_line_vals)
            # print(f"++++++++++++++++++ line_vals_list = {line_vals_list}")
            line_ids_commands = []
            if self.is_check_payment:
                print("liquidity_lines ids ==== ", liquidity_lines.ids)
                count = 0
                if len(liquidity_lines.ids) > len(line_vals_list[:-1]):
                    for id in liquidity_lines.ids[len(line_vals_list[:-1]):]:
                        line_ids_commands.append(Command.delete(id))
                for item in line_vals_list[:-1]:
                    print(count)
                    line_ids_commands.append(Command.update(liquidity_lines[count].id, item) if len(
                        liquidity_lines) > count else Command.create(item))
                    print(f" {count} --> {line_ids_commands[count]}")
                    count += 1
                
                line_ids_commands.append(Command.update(
                    counterpart_lines.id, line_vals_list[-1]) if counterpart_lines else Command.create(line_vals_list[count+1]))
                for line in writeoff_lines:
                    line_ids_commands.append((count+1, line.id))

                for extra_line_vals in line_vals_list[count+1:]:
                    line_ids_commands.append((0, 0, extra_line_vals))
                
            else:
                if len(liquidity_lines.ids) > len(line_vals_list[:-1]):
                    for id in liquidity_lines.ids[len(line_vals_list[:-1]):]:
                        line_ids_commands.append(Command.delete(id))
                line_ids_commands += [
                Command.update(liquidity_lines[0].id, line_vals_list[0]) if liquidity_lines else Command.create(line_vals_list[0]),
                Command.update(counterpart_lines.id, line_vals_list[-1]) if counterpart_lines else Command.create(line_vals_list[1])
                ]
                for line in writeoff_lines:
                    line_ids_commands.append((2, line.id))

                for extra_line_vals in line_vals_list[2:]:
                    line_ids_commands.append((0, 0, extra_line_vals))

            # Update the existing journal items.
            # If dealing with multiple write-off lines, they are dropped and a new one is generated.
            # print(f"~~~~~~~~~~~~~~~ line_ids_commands {line_ids_commands}")
            pay.move_id.with_context(skip_invoice_sync=True).write({
                'partner_id': pay.partner_id.id,
                'currency_id': pay.currency_id.id,
                'partner_bank_id': pay.partner_bank_id.id,
                'currency_rate': pay.currency_rate,
                'line_ids': line_ids_commands,
            })
            # print(f"############ pay {pay.move_id.line_ids}")
