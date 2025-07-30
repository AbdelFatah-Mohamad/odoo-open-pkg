from contextlib import contextmanager

from odoo import _, api, fields, models, Command
from odoo.addons.web.controllers.utils import clean_action
from odoo.tools.misc import formatLang
from odoo.exceptions import ValidationError

class BankRecWidget(models.Model):
    _inherit = 'bank.rec.widget'
    _description = _('Bank Rec Widget')
    
    check_id = fields.Many2one(comodel_name="account.check")
    
    def _validation_lines_vals(self, line_ids_create_command_list, aml_to_exchange_diff_vals, to_reconcile):
        partners = (self.line_ids.filtered(lambda x: x.flag != 'liquidity')).partner_id
        partner_to_set = partners if len(partners) == 1 else self.env['res.partner']
        source2exchange = self.line_ids.filtered(lambda l: l.flag == 'exchange_diff').grouped('source_aml_id')
        for line in self.line_ids:
            if line.check_id:
                if line.check_id.due_date > fields.Date.today():
                    raise ValidationError(_(f"Please Takecare That Check {line.check_id.check_no} can't be reconsiled until Due Date {line.check_id.due_date}!"))
            if line.flag == 'exchange_diff':
                continue

            amount_currency = line.amount_currency
            balance = line.balance
            if line.flag == 'new_aml':
                to_reconcile.append((len(line_ids_create_command_list) + 1, line.source_aml_id))
                exchange_diff = source2exchange.get(line.source_aml_id)
                if exchange_diff:
                    aml_to_exchange_diff_vals[len(line_ids_create_command_list) + 1] = {
                        'amount_residual': exchange_diff.balance,
                        'amount_residual_currency': exchange_diff.amount_currency,
                        'analytic_distribution': exchange_diff.analytic_distribution,
                    }
                    # Squash amounts of exchange diff into corresponding new_aml
                    amount_currency += exchange_diff.amount_currency
                    balance += exchange_diff.balance
            line_ids_create_command_list.append(Command.create(line._get_aml_values(
                sequence=len(line_ids_create_command_list) + 1,
                partner_id=partner_to_set.id if line.flag in ('liquidity', 'auto_balance') else line.partner_id.id,
                amount_currency=amount_currency,
                balance=balance,
                check_id =line.check_id.id
            )))

    def _action_validate(self):
        self.ensure_one()
        partners = (self.line_ids.filtered(lambda x: x.flag != 'liquidity')).partner_id
        partner_to_set = partners if len(partners) == 1 else self.env['res.partner']

        # Prepare the lines to be created.
        to_reconcile = []
        line_ids_create_command_list = []
        aml_to_exchange_diff_vals = {}

        self._validation_lines_vals(line_ids_create_command_list, aml_to_exchange_diff_vals, to_reconcile)

        st_line = self.st_line_id
        move = st_line.move_id
        # print("1 move state *************** ",move.state)
        # Update the move.
        move_ctx = move.with_context(
            force_delete=True,
            skip_readonly_check=True,
        )
        move_ctx.write({'partner_id': partner_to_set.id, 'line_ids': [Command.clear()] + line_ids_create_command_list})
        vals = []
        for line in move_ctx.line_ids.filtered(lambda x: x.check_id != False):
            # print("test ******** ,", line.check_id ,line.name)
            check = line.check_id
            # print("************* ",'is_transfer' not in self._context)
            check.state = "cashed"
            check.journal_id = move_ctx.journal_id.id 
            values = {
                'check_id': check.id,
                'action_date':fields.Datetime.now(),
                'state': check.state,
                'payment_state': 'bank_statment' ,
                'journal_id': move_ctx.journal_id.id,
                'move_id': move_ctx.id,
                'payment_id': check.payment_id.id,
                'partner_id': line.partner_id.id,
                'operation_type': 'reconcile'
            }
            vals.append(values)
        self.env["account.check.operation"].create(vals)
        AccountMoveLine = self.env['account.move.line']
        sequence2lines = move_ctx.line_ids.grouped('sequence')
        lines = [
            (sequence2lines[index], counterpart_aml)
            for index, counterpart_aml in to_reconcile
        ]
        all_line_ids = tuple({_id for line, counterpart in lines for _id in (line + counterpart).ids})
        # Handle exchange diffs
        exchange_diff_moves = None
        lines_with_exch_diff = AccountMoveLine
        if aml_to_exchange_diff_vals:
            exchange_diff_vals_list = []
            for line, counterpart in lines:
                line = line.with_prefetch(all_line_ids)
                counterpart = counterpart.with_prefetch(all_line_ids)
                exchange_diff_amounts = aml_to_exchange_diff_vals.get(line.sequence, {})
                exchange_analytic_distribution = exchange_diff_amounts.pop('analytic_distribution', False)
                if exchange_diff_amounts:
                    related_exchange_diff_amls = line if exchange_diff_amounts['amount_residual'] * line.amount_residual > 0 else counterpart
                    exchange_diff_vals_list.append(related_exchange_diff_amls._prepare_exchange_difference_move_vals(
                        [exchange_diff_amounts],
                        exchange_date=max(line.date, counterpart.date),
                        exchange_analytic_distribution=exchange_analytic_distribution,
                    ))
                    lines_with_exch_diff += line
            exchange_diff_moves = AccountMoveLine._create_exchange_difference_moves(exchange_diff_vals_list)

        # Perform the reconciliation.
        self.env['account.move.line'].with_context(no_exchange_difference=True)._reconcile_plan(
            [(line + counterpart).with_prefetch(all_line_ids) for line, counterpart in lines])

        # Assign exchange move to partials.
        for index, line in enumerate(lines_with_exch_diff):
            exchange_move = exchange_diff_moves[index]
            for debit_credit in ('debit', 'credit'):
                partials = line[f'matched_{debit_credit}_ids'] \
                    .filtered(lambda partial: partial[f'{debit_credit}_move_id'].move_id != exchange_move)
                partials.exchange_move_id = exchange_move

        # Fill missing partner.
        st_line_ctx = st_line.with_context(skip_account_move_synchronization=True, skip_readonly_check=True)
        st_line_ctx.partner_id = partner_to_set

        # Create missing partner bank if necessary.
        if st_line.account_number and st_line.partner_id:
            st_line_ctx.partner_bank_id = st_line._find_or_create_bank_account() or st_line.partner_bank_id

        # Refresh analytic lines.
        move.line_ids.analytic_line_ids.with_context(force_analytic_line_delete=True).unlink()
        move.line_ids.with_context(validate_analytic=True)._create_analytic_lines()
        # print("2 move state *************** ",move.state)
    
    # def _action_validate(self):
    #     # print(f"----------------------------- _action_validate")
    #     self.ensure_one()
    #     partners = (self.line_ids.filtered(lambda x: x.flag != 'liquidity')).partner_id
    #     partner_to_set = partners if len(partners) == 1 else self.env['res.partner']
    #     # Prepare the lines to be created.
    #     to_reconcile = []
    #     line_ids_create_command_list = []
    #     aml_to_exchange_diff_vals = {}
    #     # print(f" --------------------- enumerate(self.line_ids) {enumerate(self.line_ids)}")
    #     for i, line in enumerate(self.line_ids):
    #         if line.check_id:
    #             if line.check_id.due_date > fields.Date.today():
    #                 raise ValidationError(_(f"Please Takecare That Check {line.check_id.check_no} can't be reconsiled until Due Date {line.check_id.due_date}!"))
    #         if line.flag == 'exchange_diff':
    #             continue
    #         # print(f"--------------------------- line {i}, {line.name}")
    #         amount_currency = line.amount_currency
    #         balance = line.balance
    #         if line.flag == 'new_aml':
    #             to_reconcile.append((i, line.source_aml_id.id))
    #             exchange_diff = self.line_ids \
    #                 .filtered(lambda x: x.flag == 'exchange_diff' and x.source_aml_id == line.source_aml_id)
    #             if exchange_diff:
    #                 aml_to_exchange_diff_vals[i] = {
    #                     'amount_residual': exchange_diff.balance,
    #                     'amount_residual_currency': exchange_diff.amount_currency,
    #                     'analytic_distribution': exchange_diff.analytic_distribution,
    #                 }
    #                 # Squash amounts of exchange diff into corresponding new_aml
    #                 amount_currency += exchange_diff.amount_currency
    #                 balance += exchange_diff.balance
    #         # print("-------------------- line.check_id",line.check_id)
    #         line_ids_create_command_list.append(Command.create(line._get_aml_values(
    #             sequence=i,
    #             partner_id=partner_to_set.id if line.flag in ('liquidity', 'auto_balance') else line.partner_id.id,
    #             amount_currency=amount_currency,
    #             balance=balance,
    #             check_id =line.check_id.id
    #         )))

    #     st_line = self.st_line_id
    #     move = st_line.move_id
    #     # print(f" ---------------------- move {move.name}")

    #     # Update the move.
    #     move_ctx = move.with_context(
    #         skip_invoice_sync=True,
    #         skip_invoice_line_sync=True,
    #         skip_account_move_synchronization=True,
    #         force_delete=True,
    #     )
    #     move_ctx.write({'partner_id': partner_to_set.id, 'line_ids': [Command.clear()] + line_ids_create_command_list})
    #     print(f"move_ctx.payment_id.name {move_ctx.origin_payment_id.name}")
    #     move_ctx.state = 'draft'
    #     if move_ctx.state == 'draft':
    #         move_ctx.action_post()

    #     AccountMoveLine = self.env['account.move.line']
    #     lines = [
    #         (move_ctx.line_ids.filtered(lambda x: x.sequence == index),
    #          self.env['account.move.line'].browse(counterpart_aml_id))
    #         for index, counterpart_aml_id in to_reconcile
    #     ]
    #     # Handle exchange diffs
    #     exchange_diff_moves = None
    #     lines_with_exch_diff = AccountMoveLine
    #     if aml_to_exchange_diff_vals:
    #         exchange_diff_vals_list = []
    #         for line, counterpart in lines:
    #             exchange_diff_amounts = aml_to_exchange_diff_vals.get(line.sequence, {})
    #             exchange_analytic_distribution = exchange_diff_amounts.pop('analytic_distribution', False)
    #             if exchange_diff_amounts:
    #                 related_exchange_diff_amls = line if exchange_diff_amounts['amount_residual'] * line.amount_residual > 0 else counterpart
    #                 exchange_diff_vals_list.append(related_exchange_diff_amls._prepare_exchange_difference_move_vals(
    #                     [exchange_diff_amounts],
    #                     exchange_date=max(line.date, counterpart.date),
    #                     exchange_analytic_distribution=exchange_analytic_distribution,
    #                 ))
    #                 lines_with_exch_diff += line
    #         exchange_diff_moves = AccountMoveLine._create_exchange_difference_moves(exchange_diff_vals_list)

    #     # Perform the reconciliation.
    #     self.env['account.move.line'].with_context(no_exchange_difference=True)._reconcile_plan(
    #         [line + counterpart for line, counterpart in lines])

    #     # Assign exchange move to partials.
    #     for index, line in enumerate(lines_with_exch_diff):
    #         (line.matched_debit_ids + line.matched_credit_ids).exchange_move_id = exchange_diff_moves[index]

    #     # Fill missing partner.
    #     st_line_ctx = st_line.with_context(skip_account_move_synchronization=True)
    #     st_line_ctx.partner_id = partner_to_set

    #     # Create missing partner bank if necessary.
    #     if st_line.account_number and st_line.partner_id and not st_line.partner_bank_id:
    #         st_line_ctx.partner_bank_id = st_line._find_or_create_bank_account()

    #     # Refresh analytic lines.
    #     move.line_ids.analytic_line_ids.unlink()
    #     move.line_ids._create_analytic_lines()
    #     print(f"!!!!!!!! move.payment_id.name {move_ctx.origin_payment_id.name}")
