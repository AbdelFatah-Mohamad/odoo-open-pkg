from odoo import models, fields, api, _
from datetime import date
from dateutil import relativedelta

state_list = [
    ('draft', 'draft'),
    ('check_book', 'Check Book'),
    ('collected', 'Collected (CheckBox)'),
    ('deposited', 'Deposited'),
    ('cashed', 'Cashed'),
    ('returned', 'Returned'),
    ('returned_to_partner', 'Returned To Partner'),
    ('endorsed', 'Endorsed'),
    ('cancelled', 'Cancelled'),
]

class AccountCheck(models.Model):
    _name = 'account.check'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'AccountCheck'
    _order = 'id desc'

    # def default_get(self, fields_list):
    #     # res = super().default_get(fields_list)
    #     # res["due_date"] = date.today()
    #     # print("========================= ", self.payment_id ,res.get("payment_id"))
    #     # if self._context.get("default_payment_id"):
    #     #     print("------------------- =-=)( ",self.payment_id.browse(self._context.get("default_payment_id")).check_ids)
    #     #     payment_id = self.payment_id.browse(self._context.get("default_payment_id"))
    #     #     res["due_date"] = payment_id.check_ids[0].due_date + relativedelta.relativedelta(months=1) if len(payment_id.check_ids) >= 1 else payment_id.date
    #     # print("------------------- ",res)
    #     # print("------------------- ",self.journal_id)
    #     return res
    name = fields.Char(readonly=True)
    image = fields.Binary()
    state = fields.Selection(selection=state_list, default="collected", tracking=1)
    check_no = fields.Char(string="Check NO.", required=True, tracking=1)
    check_type = fields.Selection(selection=[('in', 'In'),
                                             ('out', 'Out')], string='Check Type')
    due_date = fields.Date(string="Due Date", tracking=1)
    amount = fields.Monetary(currency_field='currency_id', tracking=1)
    check_book_id = fields.Many2one(comodel_name="account.check.book",)
    currency_id = fields.Many2one(
        comodel_name="res.currency", related='journal_id.currency_id')
    bank_id = fields.Many2one(
        comodel_name="res.bank", string="Check Bank", required=True)
    journal_id = fields.Many2one(
        comodel_name="account.journal", required=True, readonly=True, tracking=1)

    check_tag_ids = fields.Many2many(
        comodel_name="account.check.tag", string="Check Tags", tracking=1)

    third_party = fields.Boolean(string="3rd Party", default=False, tracking=1)
    third_party_name = fields.Char(string="3rd Party", tracking=1)

    payment_id = fields.Many2one(comodel_name="account.payment")
    payment_type = fields.Selection([
        ('outbound', 'Send'),
        ('inbound', 'Receive'),
    ], string='Payment Type', related="payment_id.payment_type")
    payment_state = fields.Selection(related="payment_id.state")
    partner_id = fields.Many2one(
        comodel_name="res.partner", related="payment_id.partner_id", store=True, string="Customer/Vendor")
    # is_internal_transfer = fields.Boolean(
    #     related="payment_id.is_internal_transfer")
    payment_date = fields.Date(
        related="payment_id.date", string="Payment Date")

    operation_ids = fields.One2many(
        comodel_name="account.check.operation", inverse_name="check_id", readonly=True)

    cancel_reason = fields.Text(tracking=1)
    def action_cancel_state(self):
        action = self.sudo().env.ref("odoo_check_managment.action_account_check_cancel_reason").read()[0]
        action["context"] = {
            "default_check_id": self.id,
        }
        return action


    def move_check_to_return_journal_account(self):
        for check in self:
            if check.state == "returned_to_partner":
                move = self.env['account.move'].create({'move_type': 'entry',
                                        'is_check': True,
                                        'journal_check_id': check.journal_id.id,
                                        'journal_id': check.journal_id.id,
                                        'currency_id': check.currency_id.id,
                                        'checks_ids': [(6, 0, check.ids)]
                                        })
                journal_items = []
                # Transfer from outstanding payments to Liquidity transefer account
                journal_items.append({
                    'move_id': move.id,
                    'display_type': 'product',
                    'account_id': check.journal_id.return_check_account.id,
                    'name': 'Returnd '+check.name+' '+check.check_no + ' To Return Check Box',
                    'check_id': check.id,
                    'amount_currency': check.amount,
                    'currency_id': check.currency_id.id,
                    'partner_id': check.partner_id.id
                })
                print("---------------", check.currency_id.name)
                journal_items.append({
                    'move_id': move.id,
                    'account_id': check.partner_id.property_account_receivable_id.id,
                    'currency_id': check.currency_id.id,
                    'name': 'Returnd Check ' + check.check_no,
                    'amount_currency': check.amount * -1,
                    'partner_id': check.partner_id.id,
                    'check_id': check.id,
                    # 'credit': total,
                    # 'partner_id': partner_id
                })
                lines = self.env['account.move.line'].create(journal_items)
                print("---------------", check.currency_id.id)
                print("---------------", check.amount)
                move.write({
                    'line_ids': [(6, 0, lines.ids)]
                })
                move.with_context({'return_type':'check_box'}).action_post()

    # def return_cashed_checks(self):
    #     for rec in self:
    #         if rec.state == 'cashed':
    #             move = self.env['account.move'].create({'move_type': 'entry',
    #                                                     'is_check': True,
    #                                                     'journal_check_id': rec.journal_id.id,
    #                                                     'journal_id': rec.journal_id.id,
    #                                                     'currency_id': rec.currency_id.id,
    #                                                     'checks_ids': [(6, 0, rec.ids)]
    #                                                     })
    #             print('=============', move.name)
    #             print('=============', rec.ids)
    #             journal_items = []
    #             # Transfer from outstanding payments to Liquidity transefer account
    #             journal_items.append({
    #                 'move_id': move.id,
    #                 'display_type': 'product',
    #                 'account_id': self.env.company.transfer_account_id.id,
    #                 'name': 'Returnd '+rec.name+' '+rec.check_no,
    #                 'check_id': rec.id,
    #                 'amount_currency': rec.amount,
    #                 'currency_id': rec.currency_id.id,
    #                 'partner_id': rec.partner_id.id
    #             })
    #             print("---------------", rec.currency_id.name)
    #             journal_items.append({
    #                 'move_id': move.id,
    #                 'account_id': self.env.company.account_journal_payment_credit_account_id.id,
    #                 'currency_id': rec.currency_id.id,
    #                 'name': 'Returnd Check ' + rec.check_no,
    #                 'amount_currency': rec.amount * -1,
    #                 'partner_id': rec.partner_id.id
    #                 # 'credit': total,
    #                 # 'partner_id': partner_id
    #             })
    #             lines = self.env['account.move.line'].create(journal_items)
    #             print("---------------", rec.currency_id.id)
    #             print("---------------", rec.amount)
    #             move.write({
    #                 'line_ids': [(6, 0, lines.ids)]
    #             })
    #             move.action_post()

    #             journal = self.env['account.journal'].search(
    #                 [('currency_id', '=', rec.currency_id.id), ('is_returned_check_box', '=', True)])
    #             move = self.env['account.move'].create({'move_type': 'entry',
    #                                                     'is_check': True,
    #                                                     'journal_check_id': rec.journal_id.id,
    #                                                     'journal_id': journal[0].id,
    #                                                     'currency_id': rec.currency_id.id,
    #                                                     'is_return': True,
    #                                                     'checks_ids': [(6, 0, rec.ids)],
    #                                                     })
    #             print('=============', move.name)
    #             print('=============', rec.ids)
    #             journal_items.clear()
    #             # Transfer from Liquidity payments to Return check journal account
    #             journal_items.append({
    #                 'move_id': move.id,
    #                 'display_type': 'product',
    #                 'account_id': journal.return_check_account.id,
    #                 # 'account_id': rec.partner_id.property_account_receivable_id.id,
    #                 'name': 'Returnd '+rec.name+' '+rec.check_no,
    #                 'check_id': rec.id,
    #                 'amount_currency': rec.amount,
    #                 'currency_id': rec.currency_id.id,
    #                 'partner_id': rec.partner_id.id
    #             })
    #             print("---------------", rec.currency_id.name)
    #             journal_items.append({
    #                 'move_id': move.id,
    #                 'account_id': self.env.company.transfer_account_id.id,
    #                 'currency_id': rec.currency_id.id,
    #                 'name': 'Returnd Check ' + rec.check_no,
    #                 'amount_currency': rec.amount * -1,
    #                 # 'credit': total,
    #                 'partner_id': rec.partner_id.id
    #             })
    #             # Transfer from Return check journal account to partner receivable  account
    #             # journal_items.append({
    #             #     'move_id': move.id,
    #             #     'display_type':'product',
    #             #     'account_id': rec.partner_id.property_account_receivable_id.id,
    #             #     'name': 'Returnd '+rec.name+' '+rec.check_no,
    #             #     'check_id': rec.id,
    #             #     'amount_currency': rec.amount,
    #             #     'currency_id':rec.currency_id.id,
    #             #     'partner_id': rec.partner_id.id
    #             # })
    #             # print("---------------",rec.currency_id.name)
    #             # journal_items.append({
    #             #     'move_id': move.id,
    #             #     'account_id': journal.return_check_account.id,
    #             #     'currency_id':rec.currency_id.id,
    #             #     'name': 'Returnd Check '+ rec.check_no,
    #             #     'amount_currency': rec.amount * -1,
    #             #     # 'credit': total,
    #             #     # 'partner_id': partner_id
    #             # })
    #             # print('!!!!!!!!!!!!!!!!!11  ', journal_items)
    #             lines = self.env['account.move.line'].create(journal_items)
    #             # print("---------------", sum([line.debit for line in lines]))
    #             # print("---------------", sum([line.credit for line in lines]))
    #             # print("---------------", rec.amount)
    #             move.write({
    #                 'line_ids': [(6, 0, lines.ids)]
    #             })
    #             move.action_post()
    #             # rec.journal_id = journal[0].id

    @api.model
    def create(self, vals_list):
        vals_list['name'] = self.env["ir.sequence"].next_by_code("account.check")
        if 'payment_id' in vals_list:
            payment = self.env['account.payment'].browse(vals_list['payment_id'])
            vals_list['journal_id'] = payment.journal_id.id
            vals_list['currency_id'] = payment.journal_id.currency_id.id
            if payment.partner_type == 'supplier':
                vals_list['check_type'] = 'out'
            else:
                vals_list['check_type'] = 'in'
        return super(AccountCheck, self).create(vals_list)
