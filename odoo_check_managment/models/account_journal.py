from odoo import models, fields, api, _


class AccounJournal(models.Model):
    _inherit = 'account.journal'
    _description = _('Account Journal')

    checks_ids = fields.One2many(
        comodel_name="account.check", inverse_name="journal_id", string="Checks")
    check_books_ids = fields.One2many(
        comodel_name="account.check.book", inverse_name="journal_id", string="Check Books")
    is_check_box = fields.Boolean(default=False)
    is_returned_check_box = fields.Boolean(default=False)

    return_check_account = fields.Many2one(comodel_name="account.account")

    def _default_outbound_payment_methods(self):
        res = super()._default_outbound_payment_methods()
        if self.type == 'bank':
            res |= self.env.ref('odoo_check_managment.account_payment_method_check_out')
        return res
    
    def _default_inbound_payment_methods(self):
        res = super()._default_inbound_payment_methods()
        if self.is_check_box or self.is_returned_check_box:
            res |= self.env.ref('odoo_check_managment.account_payment_method_check_in')
        return res

    def _compute_available_payment_method_ids(self):
        super()._compute_available_payment_method_ids()
        if self.is_check_box or self.is_returned_check_box or self.type == 'bank':
            self.available_payment_method_ids = [
                (4, self.env.ref('odoo_check_managment.account_payment_method_check_out').id),
                (4, self.env.ref('odoo_check_managment.account_payment_method_check_in').id)]