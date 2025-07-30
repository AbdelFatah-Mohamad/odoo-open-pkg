from odoo import models, fields, api, _, Command
from odoo.exceptions import ValidationError, UserError
from odoo.tools.misc import format_date, formatLang
from dateutil import relativedelta
from datetime import datetime, timedelta
from math import fsum


class AccountPayment(models.Model):
    _inherit = 'account.payment'
    _description = 'AccountPayment'

    descriptions = fields.Text()
    main_payment_id = fields.Many2one("account.payment",)
    multi_payments_ids = fields.One2many(
        string=_('Multi Payments'),
        comodel_name='account.payment',
        inverse_name='main_payment_id',
    )
    check_ids = fields.One2many(
        comodel_name="account.check", inverse_name="payment_id")
    # @api.onchange('check_ids.check_no')
    # def _onchange_check_ids(self):
    #     print("-------------------- dasdsadsad", self.check_ids)
    #     if len(self.check_ids) > 1:
    #         self.check_ids[-1].due_date = self.check_ids[-2].due_date + relativedelta.relativedelta(months=1)
                 
        
    total_check_amount = fields.Monetary(
        currency_field="currency_id", compute="_compute_total_check_amount",digits=2)
    due_amount = fields.Monetary(currency_field="currency_id")
    checks_count = fields.Integer(compute="_compute_checks_count")

    check_book_id = fields.Many2one(comodel_name="account.check.book")
    checks_many_ids = fields.Many2many(comodel_name='account.check',
                                       relation="payments_ckecks", column1="payments", column2="checks",
                                       # domain=[('journal_id','=',lambda self: self.journal_id.id)],
                                       string="Checks")

    is_check_payment = fields.Boolean(
        string=_('Is check payment?'), compute='_compute_is_check_payment', store=True)
    
    @api.depends('journal_id', 'payment_method_line_id')
    @api.onchange('journal_id', 'payment_method_line_id')
    def _compute_is_check_payment(self):
        for rec in self:
            rec.is_check_payment = True if rec.payment_method_id.code in ['checks_in', 'checks_out'] else False

    multi_payments = fields.Boolean(string="Multi Payment?")

    journal_type = fields.Selection(related='journal_id.type')
    is_journal_check_box = fields.Boolean(related='journal_id.is_check_box')

    @api.depends('check_ids.amount', 'amount', 'checks_many_ids.amount')
    def _compute_total_check_amount(self):
        for rec in self:
            total = 0.0
            print(list(map(lambda re: re.check_id,
                  rec.reconciled_statement_line_ids.line_ids)))
            if (rec.partner_type == 'supplier' and rec.journal_id.is_check_box) or rec.check_book_id:
                for check in rec.checks_many_ids:
                    if rec.currency_id.id != check.currency_id.id:
                        raise ValidationError(
                            f"Please take care that check and payment currency are different")
                    total += check.amount
            else:
                for check in rec.check_ids:
                    # print(f"%%%%%%%%%%%%%%%%%%%% {check.amount}")
                    if rec.currency_id.id != check.currency_id.id:
                        raise ValidationError(
                            f"Please take care that check and payment currency are different")
                    total += check.amount
            rec.total_check_amount = round(total,2)
            rec.due_amount = rec.amount - rec.total_check_amount

    @api.depends('check_ids')
    def _compute_checks_count(self):
        for rec in self:
            rec.checks_count = len(rec.check_ids) if rec.check_ids else len(
                rec.checks_many_ids) or 0

    def button_open_checks(self):
        return {
            'name': _("Checks"),
            'type': 'ir.actions.act_window',
            'res_model': 'account.check',
            'view_mode': 'list,form',
            'target': 'current',
            'domain': ['|', ('id', 'in', self.check_ids.ids), ('id', 'in', self.checks_many_ids.ids)],
        }

    def action_post(self):
        for payment in self:
            # payment.journal_id.type
            if payment.is_check_payment:
                if payment.check_book_id:
                    for check in payment.checks_many_ids:
                        if check.payment_id:
                            if check.payment_id.id != payment.id:
                                raise ValidationError(_(f"Check with NO. {check.check_no} used in Payment {check.payment_id.display_name}."))
            if payment.amount == 0:
                raise ValidationError("Amount Can't be Zero!")
            if round(payment.total_check_amount,2) != payment.amount and (len(payment.check_ids) > 0 or len(payment.checks_many_ids) > 0):
                raise ValidationError(
                    f"Total checks amount don't cover payment amount, \n Total Checks amount {payment.total_check_amount} and Payment Amount {payment.amount}")
        if len(self.multi_payments_ids) > 0:
            self.multi_payments_ids.action_post()
        super(AccountPayment, self).action_post()
        
        #  Update the related check objects with the new state of the payment object
        list_values = []
        for payment in self:
            if payment.is_check_payment:
                #  We need to update states of checks and create operation records
                if (payment.partner_type == 'supplier' and payment.journal_id.is_check_box) or payment.check_book_id:
                    # In internal transfer case work with Many2many field
                    for check in payment.checks_many_ids:
                        if (payment.payment_type == 'outbound' and payment.journal_id.is_check_box and payment.partner_type == 'supplier') or (payment.payment_type == 'outbound' and payment.check_book_id):
                            check.state = 'endorsed'
                        elif (payment.journal_id.is_check_box or payment.journal_id.is_returned_check_box):
                            check.state = 'deposited'
                        elif not (payment.journal_id.is_check_box or payment.journal_id.is_returned_check_box):
                            check.state = 'returned'
                        else:
                            check.state = 'collected'
                        if payment.check_book_id:
                            check.payment_id = self.id
                        values = {
                            'check_id': check.id,
                            'action_date': datetime.now(),
                            'state': check.state,
                            'journal_id': payment.journal_id.id,
                            'payment_id': payment.id,
                            'payment_state': str(payment.state),
                            'move_id': payment.move_id.id,
                            'partner_id': payment.partner_id.id,
                            'operation_type': "payment"
                        }
                        # if payment.is_internal_transfer:
                        #     values['destination_journal_id'] = payment.destination_journal_id.id
                        # if not payment.move_id in check.operation_ids.move_id:
                        list_values.append(values)
                        # else:
                        #     check.write({'operation_ids':[(0, 0, values)]})
                else:
                    # In normal paymet deal with one2many field
                    for check in payment.check_ids:
                        check.journal_id = payment.journal_id.id
                        # In case vendor and send
                        if payment.payment_type == 'outbound' and payment.partner_type == 'supplier':
                            check.state = 'endorsed'
                        else:
                            check.state = 'collected'
                        values = {
                            'check_id': check.id,
                            'action_date': datetime.now(),
                            'state': check.state,
                            'payment_state': payment.state,
                            'journal_id': payment.journal_id.id,
                            'move_id': payment.move_id.id,
                            'payment_id': payment.id,
                            'partner_id': payment.partner_id.id,
                            'operation_type': 'payment'
                        }
                        # if not payment.move_id in check.operation_ids.move_id:
                        list_values.append(values)
                        # else:
                        #     check.write({'operation_ids':[(0, 0, values)]})
            self.env["account.check.operation"].create(list_values)
        # if self.payment_id:
        #     list_values = []
        #     if self.payment_id.is_check_payment:
        #         #  We need to update states of checks and create operation records
        #         if self.payment_id.is_internal_transfer or (self.payment_id.partner_type == 'supplier' and self.payment_id.journal_id.is_check_box):
        #             # In internal transfer case work with Many2many field
        #             for check in self.payment_id.checks_many_ids:
        #                 check.journal_id = self.payment_id.destination_journal_id.id if self.payment_id.is_internal_transfer else self.payment_id.journal_id.id
        #                 if self.payment_id.payment_type == 'outbound' and self.payment_id.journal_id.is_check_box and not self.payment_id.destination_journal_id.is_check_box:
        #                     check.state = 'deposited'
        #                 elif self.payment_id.payment_type == 'inbound' and not self.payment_id.journal_id.is_check_box and self.payment_id.destination_journal_id.is_check_box:
        #                     check.state = 'collected'
        #                 else:
        #                     check.state = 'collected'
        #                 values = {
        #                     'check_id': check.id,
        #                     'action_date': datetime.now(),
        #                     'state': check.state,
        #                     'journal_id': self.payment_id.journal_id.id,
        #                     'payment_id': self.payment_id.id,
        #                     'payment_state': str(self.payment_id.state),
        #                     'move_id': self.payment_id.move_id.id,
        #                     'partner_id': self.payment_id.partner_id.id,
        #                     'operation_type': "internal"
        #                 }
        #                 if self.payment_id.is_internal_transfer:
        #                     values['destination_journal_id'] = self.payment_id.destination_journal_id.id
        #                 # if not self.payment_id.move_id in check.operation_ids.move_id:
        #                 list_values.append(values)
        #                 # else:
        #                 #     check.write({'operation_ids':[(0, 0, values)]})
        #         else:
        #             # In normal paymet deal with one2many field
        #             for check in self.payment_id.check_ids:
        #                 check.journal_id = self.payment_id.journal_id.id
        #                 # In case vendor and send
        #                 if self.payment_id.payment_type == 'outbound' and self.payment_id.partner_type == 'supplier':
        #                     check.state = 'endorsed'
        #                 else:
        #                     check.state = 'collected'
        #                 values = {
        #                     'check_id': check.id,
        #                     'action_date': datetime.now(),
        #                     'state': check.state,
        #                     'payment_state': self.payment_id.state,
        #                     'journal_id': self.payment_id.journal_id.id,
        #                     'move_id': self.id,
        #                     'payment_id': self.payment_id.id,
        #                     'partner_id': self.payment_id.partner_id.id,
        #                     'operation_type': 'payment'
        #                 }
        #                 # if not self.move_id in check.operation_ids.move_id:
        #                 list_values.append(values)
        #                 # else:
        #                 #     check.write({'operation_ids':[(0, 0, values)]})
        #         self.env["account.check.operation"].create(list_values)

    def action_draft(self):
        super(AccountPayment, self).action_draft()
        if len(self.multi_payments_ids) > 0:
            self.multi_payments_ids.action_draft()
        list_values = []
        if self.is_check_payment:
            if (self.partner_type == 'supplier' and self.journal_id.is_check_box) or self.check_book_id:
                for check in self.checks_many_ids:
                    if self.check_book_id:
                        if check.payment_id.id == self.id:                        
                            check.payment_id = False
                            check.state = "check_book"
                        else:
                            continue
                    else :
                        check.state = "collected"
                    values = {
                        'check_id': check.id,
                        'action_date': datetime.now(),
                        'state': check.state,
                        'journal_id': self.journal_id.id,
                        'payment_id': self.id,
                        'payment_state': str(self.state),
                        'move_id': self.move_id.id,
                        'partner_id': self.partner_id.id,
                        'operation_type': "internal"
                    }
                    # if not self.move_id in check.operation_ids.move_id:
                    # list_values.append(values)
                    # else:
                    #     check.write({'operation_ids':[(0, 0, values)]})
                    list_values.append(values)
            else:
                for check in self.check_ids:
                    check.state = "collected"
                    values = {
                        'check_id': check.id,
                        'action_date': datetime.now(),
                        'state': check.state,
                        'journal_id': self.journal_id.id,
                        'payment_id': self.id,
                        'payment_state': str(self.state),
                        'move_id': self.move_id.id,
                        'partner_id': self.partner_id.id,
                        'operation_type': "internal"
                    }
                    list_values.append(values)
            self.env["account.check.operation"].create(list_values)

    def action_cancel(self):
        super(AccountPayment, self).action_cancel()
        if len(self.multi_payments_ids) > 0:
            self.multi_payments_ids.action_cancel()
        list_values = []
        if self.check_book_id:
            for check in self.checks_many_ids:
                if self.check_book_id:
                    if check.payment_id.id == self.id:                        
                        check.payment_id = False
                        check.state = "check_book"
                        print("---------------- test")
                    else:
                        continue
                else :
                    check.state = "collected"
                values = {
                    'check_id': check.id,
                    'action_date': datetime.now(),
                    'state': check.state,
                    'journal_id': self.journal_id.id,
                    'payment_id': self.id,
                    'payment_state': str(self.state),
                    'move_id': self.move_id.id,
                    'partner_id': self.partner_id.id,
                    'operation_type': "cancel"
                }
                list_values.append(values)
        else:
            for check in self.check_ids:
                check.state = 'draft'
                values = {
                    'check_id': check.id,
                    'action_date': datetime.now(),
                    'state': check.state,
                    'journal_id': self.journal_id.id,
                    'payment_id': self.id,
                    'payment_state': str(self.state),
                    'move_id': self.move_id.id,
                    'partner_id': self.partner_id.id,
                    'operation_type': "cancel"
                }
                list_values.append(values)
        self.env["account.check.operation"].create(list_values)

    @api.model
    def _get_trigger_fields_to_synchronize(self):
        res = super()._get_trigger_fields_to_synchronize()
        return res+('check_ids', 'check_many_ids')
    
    # def _synchronize_to_moves(self, changed_fields):
    #     ''' Update the account.move regarding the modified account.payment.
    #     :param changed_fields: A list containing all modified fields on account.payment.
    #     '''
    #     if self._context.get('skip_account_move_synchronization'):
    #         return

    #     if not any(field_name in changed_fields for field_name in self._get_trigger_fields_to_synchronize()):
    #         return

    #     for pay in self.with_context(skip_account_move_synchronization=True):
    #         liquidity_lines, counterpart_lines, writeoff_lines = pay._seek_for_lines()

    #         # Make sure to preserve the write-off amount.
    #         # This allows to create a new payment with custom 'line_ids'.

    #         write_off_line_vals = []
    #         if liquidity_lines and counterpart_lines and writeoff_lines:
    #             write_off_line_vals.append({
    #                 'name': writeoff_lines[0].name,
    #                 'account_id': writeoff_lines[0].account_id.id,
    #                 'partner_id': writeoff_lines[0].partner_id.id,
    #                 'currency_id': writeoff_lines[0].currency_id.id,
    #                 'amount_currency': sum(writeoff_lines.mapped('amount_currency')),
    #                 'balance': sum(writeoff_lines.mapped('balance')),
    #             })

    #         line_vals_list = pay._prepare_move_line_default_vals(write_off_line_vals=write_off_line_vals)

    #         line_ids_commands = [
    #             Command.update(liquidity_lines.id, line_vals_list[0]) if liquidity_lines else Command.create(line_vals_list[0]),
    #             Command.update(counterpart_lines.id, line_vals_list[1]) if counterpart_lines else Command.create(line_vals_list[1])
    #         ]

    #         for line in writeoff_lines:
    #             line_ids_commands.append((2, line.id))

    #         for extra_line_vals in line_vals_list[2:]:
    #             line_ids_commands.append((0, 0, extra_line_vals))

    #         # Update the existing journal items.
    #         # If dealing with multiple write-off lines, they are dropped and a new one is generated.

    #         pay.move_id\
    #             .with_context(skip_invoice_sync=True)\
    #             .write({
    #                 'partner_id': pay.partner_id.id,
    #                 'currency_id': pay.currency_id.id,
    #                 'partner_bank_id': pay.partner_bank_id.id,
    #                 'line_ids': line_ids_commands,
    #             })

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
            print(f"******* {liquidity_lines}")
            write_off_line_vals = []
            if liquidity_lines and counterpart_lines and writeoff_lines:
                write_off_line_vals.append({
                    'name': writeoff_lines[0].name,
                    'account_id': writeoff_lines[0].account_id.id,
                    'partner_id': writeoff_lines[0].partner_id.id,
                    'currency_id': writeoff_lines[0].currency_id.id,
                    'amount_currency': sum(writeoff_lines.mapped('amount_currency')),
                    'balance': sum(writeoff_lines.mapped('balance')),
                })

            line_vals_list = pay._prepare_move_line_default_vals(
                write_off_line_vals=write_off_line_vals)
            print(f"++++++++++++++++++ line_vals_list = {line_vals_list}")
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
            print(f"~~~~~~~~~~~~~~~ line_ids_commands {line_ids_commands}")
            pay.move_id.with_context(skip_invoice_sync=True).write({
                'partner_id': pay.partner_id.id,
                'currency_id': pay.currency_id.id,
                'partner_bank_id': pay.partner_bank_id.id,
                'line_ids': line_ids_commands,
            })
            print(f"############ pay {pay.move_id.line_ids}")

    def _synchronize_from_moves(self, changed_fields):
        ''' Update the account.payment regarding its related account.move.
        Also, check both models are still consistent.
        :param changed_fields: A set containing all modified fields on account.move.
        '''
        if self._context.get('skip_account_move_synchronization'):
            return

        for pay in self.with_context(skip_account_move_synchronization=True):

            # After the migration to 14.0, the journal entry could be shared between the account.payment and the
            # account.bank.statement.line. In that case, the synchronization will only be made with the statement line.
            if pay.move_id.statement_line_id:
                continue

            move = pay.move_id
            move_vals_to_write = {}
            payment_vals_to_write = {}

            if 'journal_id' in changed_fields:
                if pay.journal_id.type not in ('bank', 'cash'):
                    raise UserError(
                        _("A payment must always belongs to a bank or cash journal."))

            if 'line_ids' in changed_fields:
                all_lines = move.line_ids
                liquidity_lines, counterpart_lines, writeoff_lines = pay._seek_for_lines()
                # print(f"------------------------------ {liquidity_lines}")
                # print(f"------------------------------ {counterpart_lines}")
                # print(f"------------------------------ {writeoff_lines}")
                if len(liquidity_lines) != 1 and not (self.check_ids or self.checks_many_ids):
                    raise UserError(_(
                        "Journal Entry %s is not valid. In order to proceed, the journal items must "
                        "include one and only one outstanding payments/receipts account.",
                        move.display_name,
                    ))

                if len(counterpart_lines) != 1:
                    raise UserError(_(
                        "Journal Entry %s is not valid. In order to proceed, the journal items must "
                        "include one and only one receivable/payable account (with an exception of "
                        "internal transfers).",
                        move.display_name,
                    ))

                if any(line.currency_id != all_lines[0].currency_id for line in all_lines):
                    raise UserError(_(
                        "Journal Entry %s is not valid. In order to proceed, the journal items must "
                        "share the same currency.",
                        move.display_name,
                    ))

                if any(line.partner_id != all_lines[0].partner_id for line in all_lines):
                    raise UserError(_(
                        "Journal Entry %s is not valid. In order to proceed, the journal items must "
                        "share the same partner.",
                        move.display_name,
                    ))

                if counterpart_lines.account_id.account_type == 'asset_receivable':
                    partner_type = 'customer'
                else:
                    partner_type = 'supplier'

                liquidity_amount = fsum(
                    [line.amount_currency for line in liquidity_lines])

                print(f'liquidity_amount ::::: {liquidity_amount}')
                move_vals_to_write.update({
                    'currency_id': liquidity_lines.currency_id.id,
                    'partner_id': liquidity_lines.partner_id.id,
                })
                payment_vals_to_write.update({
                    'amount': abs(liquidity_amount),
                    'partner_type': partner_type,
                    'currency_id': liquidity_lines.currency_id.id,
                    'destination_account_id': counterpart_lines.account_id.id,
                    'partner_id': liquidity_lines.partner_id.id,
                })
                if liquidity_amount > 0.0:
                    payment_vals_to_write.update({'payment_type': 'inbound'})
                elif liquidity_amount < 0.0:
                    payment_vals_to_write.update({'payment_type': 'outbound'})

            move.write(move._cleanup_write_orm_values(
                move, move_vals_to_write))
            pay.write(move._cleanup_write_orm_values(
                pay, payment_vals_to_write))

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

        liquidity_balance = self.currency_id._convert(
            liquidity_amount_currency,
            self.company_id.currency_id,
            self.company_id,
            self.date,
        )
        counterpart_amount_currency = -liquidity_amount_currency - write_off_amount_currency
        counterpart_balance = -liquidity_balance - write_off_balance
        currency_id = self.currency_id.id
        counterpart_line_name = ''.join(
            x[1] for x in self._get_aml_default_display_name_list())

        checks = self._get_check_lines()
        print(f"______________________ checks {checks}")
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
                liquidity_balance = self.currency_id._convert(
                    liquidity_amount_currency,
                    self.company_id.currency_id,
                    self.company_id,
                    self.date,
                )
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

    def _get_check_lines(self):
        if (self.partner_type == 'supplier' and self.journal_id.is_check_box) or self.check_book_id :
            # print(f" -------------------------- _get_check_lines self.checks_many_ids")
            return self.checks_many_ids if self.checks_many_ids else False
        else:
            # print(
            #     f" -------------------------- _get_check_lines self.check_ids {self.check_ids}")
            return self.check_ids if self.check_ids else False

    def _get_liquidity_check_display_name(self, check):
        return f"Check NO {check.check_no} from bank {check.bank_id.name} with amount {check.amount}"

    # def create(self,vals):
    #     return super(AccountPayment,self).create(vals)

    # def write(self, vals):
    #     # OVERRIDE
    #     res = super().write(vals)

    #     self._synchronize_to_moves(set(vals.keys()))
    #     return res

    def unlink(self):
        for rec in self:
            if rec.check_ids:
                raise ValidationError(
                    "Can't delete payment please delete it's checks first!")
            if rec.checks_many_ids and rec.state in ['in_process', 'paid']:
                raise ValidationError(_(f"Please Cancel or Reset To Draft before Delete The Payment."))

        return super(AccountPayment, self).unlink()
