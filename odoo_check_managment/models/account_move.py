from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime


class AccountMove(models.Model):
    _inherit = 'account.move'
    _description = _('Account Move')

    checks_ids = fields.Many2many('account.check', string='Checks')
    is_check = fields.Boolean("Is check!")
    is_return = fields.Boolean(default=False)
    journal_check_id = fields.Many2one(
        comodel_name="account.journal", string="Checks journal")
    account_check_id = fields.Many2one('account.account', string='Account Check')

    @api.depends('move_type', 'is_check')
    def _compute_invoice_filter_type_domain(self):
        for move in self:
            if move.is_sale_document(include_receipts=True):
                move.invoice_filter_type_domain = 'sale'
            elif move.is_purchase_document(include_receipts=True):
                move.invoice_filter_type_domain = 'purchase'
            elif move.is_check:
                move.invoice_filter_type_domain = 'check'
            else:
                move.invoice_filter_type_domain = False

    @api.depends('company_id', 'invoice_filter_type_domain')
    def _compute_suitable_journal_ids(self):
        for m in self:
            journal_type = m.invoice_filter_type_domain or 'general'
            if journal_type == "check":
                journal_type = ("cash", "bank", "general")
            else:
                journal_type = [journal_type]
            # print("----------------- journal_type",journal_type)
            company = m.company_id or self.env.company
            m.suitable_journal_ids = self.env['account.journal'].search([
                *self.env['account.journal']._check_company_domain(company),
                ('type', 'in', journal_type),
            ])
    def _post(self, soft=False):
        # print("################# enter _post")
        # print("&&&&&&&&&&&&&&&777", self.line_ids.check_id)
        res = super()._post(soft)
        #print('--------------------', self.payment_id.name)
        # Update the related check objects with the new state of the payment object
        if self.line_ids.check_id and not self.origin_payment_id and not self.is_check:
            #print("$$$$$$$$$$$$$4 operations")
            vals = []
            for line in self.line_ids.check_id:
                # print("test ******** ,", line ,line.name)
                check = line
                # print("************* ",'is_transfer' not in self._context)
                if 'is_transfer' not in self._context:
                    check.state = "cashed" if check.state != "cashed" else "returned"
                else:
                    if check.journal_id.id != self.journal_id.id:
                        check.state = "deposited" if check.state in ("check_book", "collected") else "collected"  
                # if (check.state == "returned" or self.is_return) and 'is_transfer' not in self._context:
                #     continue
                values = {
                    'check_id': check.id,
                    'action_date': datetime.now(),
                    'state': check.state,
                    'payment_state': 'bank_statment' if not self._context.get("is_transfer",False) else False,
                    'journal_id': self.journal_id.id,
                    'move_id': self.id,
                    'payment_id': self.origin_payment_id.id,
                    'partner_id': line.partner_id.id,
                    'operation_type': 'reconcile' if not self._context.get("is_transfer",False) else 'internal'
                }
                if check.journal_id.id != self.journal_id.id:
                    values.update({
                        'destination_journal_id': self.journal_id.id,
                    })
                    check.journal_id = self.journal_id.id
                vals.append(values)
            self.env["account.check.operation"].create(vals)
        # print('~~~~~~~~~~~~~~~~~~~~',list(map(lambda x : x.matching_number,self.line_ids)))
        return res

    def action_post(self):
        for acc in self:
            # print(f"=============== journal Entry")
            partner = False
            if acc.is_check:
                check_total = 0
                for check in acc.checks_ids:
                    check_total += check.amount
                line_total = 0
                partner = False
                for line in acc.line_ids:
                    line_total += line.amount_currency if line.amount_currency > 0 else 0
                    partner = line.partner_id.id if line.partner_id else partner
            # if check_total != line_total:
            #     raise ValidationError(
            #         f"Amounts not same checks total: {check_total}, journal items: {line_total} ")
        super(AccountMove, self).action_post()
        # for line in self.line_ids:
            # print('--------------------', line.check_id.name)
        for acc in self:
            if acc.is_check:
                list_values = []
                for check in acc.checks_ids:
                    check.journal_id = acc.journal_id.id
                    if 'return_type' in acc._context:
                        check.state = 'returned' if acc._context['return_type'] == 'check_box' else 'returned_to_partner'
                    values = {
                        'check_id': check.id,
                        'action_date': datetime.now(),
                        'state': check.state,
                        'journal_id': acc.journal_id.id,
                        'move_id': acc.id,
                        'partner_id': partner,
                        'operation_type': "internal"
                    }
                    if check.journal_id.id != acc.journal_id.id:
                        values.update({
                            'destination_journal_id': acc.journal_id.id,
                        })
                    list_values.append(values)
                self.env["account.check.operation"].create(list_values)

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
                    'amount_currency': -line.amount_currency,
                    'currency_id': line.currency_id.id or check.currency_id.id,
                })
                total_amount_currency += (line.amount_currency)
                curruncy = line.currency_id.id or check.currency_id.id
                
            # print("========== ",acc.journal_id.default_account_id.name)
            # print("========== ",curruncy)
            lines.append({
                'account_id': self.account_check_id.id,
                'name': "Transerfer checks",
                'move_id': self.id,
                'amount_currency': total_amount_currency,
                'currency_id': curruncy,
            })
            self.env['account.move.line'].create(lines)


    def write(self, vals):
        res = super().write(vals)
        # print("------------- id",self.checks_ids)
        # print("------------- res",self.journal_id)
        # print("------------- res",self.journal_check_id)
        # print("------------- vals",vals)
        for acc in self:
            vals = acc.create_return_lines(vals)
        return res
