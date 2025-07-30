# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _, Command
from odoo.tools.misc import format_date, formatLang
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    currency_rate = fields.Float(
        string="Currency Rate", store=True, precompute=True, readonly=False, compute="_compute_currency_rate")

    confirmed_rate = fields.Char(string="Confirmed Rate")
    
    @api.onchange('currency_id', 'date', 'company_currency_id', 'company_id')
    @api.depends('currency_id', 'date', 'company_currency_id', 'company_id')
    def _compute_currency_rate(self):
        """
            Recompute the currency rate based on changes in currency or date.
            This method is decorated with @api.depends to specify the fields upon which it depends.
            Args:
                self: The recordset on which the method is called.
            Returns:
                No direct return value. The currency rate for each record is recomputed based on changes
                in 'currency_id' or 'date' fields.
            """

        for record in self:
            if record.currency_id:
                # print(" -------------- ",1/rates.get(record.currency_id._get_rates(record.company_id, record.date)))
                # if not record.confirmed_rate or record.confirmed_rate == '':
                rates = record.currency_id._get_rates(
                    record.company_id, record.date)
                rate = rates.get(record.currency_id.id, False)  # Default to 1 if None
                record.currency_rate = 1 / rate if rate else 1
                # else:
                #     record.currency_rate = record.confirmed_rate

    def action_post(self):
        """
            Perform a post operation with optional soft behavior.
            Args:
                soft (bool, optional): If True, performs a soft post. Defaults to True.
            Returns:
                Whatever is returned by the super()._post() method.
            """
        posted = super().action_post()
        for pay in self:
            pay.confirmed_rate = pay.currency_rate
        return posted

    # def write(self, vals):
    #     res = super().write(vals)
    #     date = vals['date'] if 'date' in vals else self.date
    #     currency_id = vals['currency_id'] if 'currency_id' in vals else self.currency_id.id
    #     currency_rate = vals['currency_rate'] if 'currency_rate' in vals else self.currency_rate
    #     currency = self.env['res.currency.rate'].search(
    #         [('currency_id', '=', currency_id), ('name', '=', date)])
    #     if not currency:
    #         self.env['res.currency.rate'].create({
    #             'name': date,
    #             'company_rate': (1 / currency_rate if currency_rate > 0 else 1),
    #             'currency_id': currency_id
    #         })
    #     else:
    #         currency.write({
    #             'company_rate': (1 / currency_rate if currency_rate > 0 else 1)})
    #     self.move_id.write({"currency_rate": currency_rate if currency_rate > 0 else 1})
    #     for move_line in self.move_id.line_ids:
    #         move_line.write({"currency_rate": (1 / currency_rate if currency_rate > 0 else 1)})
    #     return res

    # def create(self, vals):
    #     res = super().create(vals)
    #     if res['move_type'] in ['out_invoice', 'in_invoice']:
    #         date = res['date'] if 'date' in res else self.date
    #         currency_id = res['currency_id'].id if 'currency_id' in res else self.currency_id.id
    #         currency_rate = res['currency_rate'] if 'currency_rate' in res else self.currency_rate
    #         currency = self.env['res.currency.rate'].search(
    #             [('currency_id', '=', currency_id), ('name', '=', date)])
    #         if not currency:
    #             self.env['res.currency.rate'].create({
    #                 'name': date,
    #                 'company_rate': (1 / currency_rate if currency_rate > 0 else 1),
    #                 'currency_id': currency_id
    #             })
    #         else:
    #             currency.write({
    #                 'company_rate': (1 / currency_rate if currency_rate > 0 else 1)})
    #     return res
    @api.model
    def _get_trigger_fields_to_synchronize(self):
        res: tuple = super()._get_trigger_fields_to_synchronize()
        return res + ('currency_rate',)

    def _prepare_move_line_default_vals(self, write_off_line_vals=None, force_balance=None):
        ''' Prepare the dictionary to create the default account.move.lines for the current payment.
        :param write_off_line_vals: Optional list of dictionaries to create a write-off account.move.line easily containing:
            * amount:       The amount to be added to the counterpart amount.
            * name:         The label to set on the line.
            * account_id:   The account on which create the write-off.
        :param force_balance: Optional balance.
        :return: A list of python dictionary to be passed to the account.move.line's 'create' method.
        '''
        self.ensure_one()
        write_off_line_vals = write_off_line_vals or []

        if not self.outstanding_account_id:
            raise UserError(_(
                "You can't create a new payment without an outstanding payments/receipts account set either on the company or the %(payment_method)s payment method in the %(journal)s journal.",
                payment_method=self.payment_method_line_id.name, journal=self.journal_id.display_name))

        # Compute amounts.
        write_off_line_vals_list = write_off_line_vals or []
        write_off_amount_currency = sum(x['amount_currency'] for x in write_off_line_vals_list)
        write_off_balance = sum(x['balance'] for x in write_off_line_vals_list)

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
        else:
            liquidity_balance = liquidity_amount_currency * self.currency_rate
        counterpart_amount_currency = -liquidity_amount_currency - write_off_amount_currency
        counterpart_balance = -liquidity_balance - write_off_balance
        currency_id = self.currency_id.id

        # Compute a default label to set on the journal items.
        liquidity_line_name = ''.join(x[1] for x in self._get_aml_default_display_name_list())
        counterpart_line_name = ''.join(x[1] for x in self._get_aml_default_display_name_list())
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
                'currency_rate': 1/self.currency_rate
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
                'currency_rate': 1/self.currency_rate
            },
        ]
        return line_vals_list + write_off_line_vals_list



    def _synchronize_to_moves(self, changed_fields):
        '''
            Update the account.move regarding the modified account.payment.
            :param changed_fields: A list containing all modified fields on account.payment.
        '''
        if not any(field_name in changed_fields for field_name in self._get_trigger_fields_to_synchronize()):
            return

        for pay in self:
            liquidity_lines, counterpart_lines, writeoff_lines = pay._seek_for_lines()
            # Make sure to preserve the write-off amount.
            # This allows to create a new payment with custom 'line_ids'.
            write_off_line_vals = []
            if liquidity_lines and counterpart_lines and writeoff_lines:
                write_off_line_vals.append({
                    'name': writeoff_lines[0].name,
                    'account_id': writeoff_lines[0].account_id.id,
                    'partner_id': writeoff_lines[0].partner_id.id,
                    'currency_id': writeoff_lines[0].currency_id.id,
                    'amount_currency': sum(writeoff_lines.mapped('amount_currency')),
                    'balance': sum(writeoff_lines.mapped('balance')),
                    'currency_rate' :1/self.currency_rate
                })
            line_vals_list = pay._prepare_move_line_default_vals(write_off_line_vals=write_off_line_vals)
            line_ids_commands = [
                Command.update(liquidity_lines.id, line_vals_list[0]) if liquidity_lines else Command.create(line_vals_list[0]),
                Command.update(counterpart_lines.id, line_vals_list[1]) if counterpart_lines else Command.create(line_vals_list[1])
            ]
            for line in writeoff_lines:
                line_ids_commands.append((2, line.id))
            for extra_line_vals in line_vals_list[2:]:
                line_ids_commands.append((0, 0, extra_line_vals))
            # Update the existing journal items.
            # If dealing with multiple write-off lines, they are dropped and a new one is generated.
            pay.move_id \
                .with_context(skip_invoice_sync=True) \
                .write({
                'partner_id': pay.partner_id.id,
                'currency_id': pay.currency_id.id,
                'currency_rate': pay.currency_rate,
                'partner_bank_id': pay.partner_bank_id.id,
                'line_ids': line_ids_commands,
            })
