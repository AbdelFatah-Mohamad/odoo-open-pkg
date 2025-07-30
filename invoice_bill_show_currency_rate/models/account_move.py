from odoo import fields, models, api, _
from odoo.exceptions import ValidationError


class AccountMove(models.Model):
    _inherit = "account.move"

    # def default_get(self, fields_list):
    #     res = super().default_get(fields_list)
    #     currency = self.env["res.currency"].browse(res['currency_id'])
    #     print('------------------ currency_rate',res["currency_rate"])
    #     res["currency_rate"] = currency.inverse_rate
    #     return res

    currency_rate = fields.Float(
        string="Currency Rate", readonly=False, compute="_compute_currency_rate", precompute=True, store=True)
    confirmed_rate = fields.Char(string="Confirmed Rate")
    invoice_date = fields.Date(
        string='Invoice/Bill Date',
        index=True,
        copy=False,
        default=fields.Date.today()
    )

    @api.onchange('currency_id', 'invoice_date', 'company_currency_id', 'company_id')
    @api.depends('currency_id', 'company_currency_id', 'company_id', 'invoice_date')
    def _compute_currency_rate(self):
        """
            Recompute the currency rate based on changes in currency or invoice_date.
            This method is decorated with @api.depends to specify the fields upon which it depends.
            Args:
                self: The recordset on which the method is called.
            Returns:
                No direct return value. The currency rate for each record is recomputed based on changes
                in 'currency_id' or 'invoice_date' fields.
            """

        for record in self:
            if record.currency_id:
                # print(" -------------- ",1/rates.get(record.currency_id._get_rates(record.company_id, record.invoice_date)))
                # if not record.confirmed_rate or record.confirmed_rate == '':
                if record.origin_payment_id and record.origin_payment_id.currency_rate:
                    record.currency_rate = record.origin_payment_id.currency_rate
                    continue
                if record.date:
                    rates = record.currency_id._get_rates(
                        record.company_id, record.date)
                    rate = rates.get(record.currency_id.id, 1)
                    record.currency_rate = 1 / rate if rate else 1
                else:
                    rates = record.currency_id._get_rates(
                        record.company_id, fields.Date.today())
                    rate = rates.get(record.currency_id.id, 1)
                    record.currency_rate = 1 / rate if rate else 1

    def _post(self, soft=True):
        """
            Perform a post operation with optional soft behavior.
            Args:
                soft (bool, optional): If True, performs a soft post. Defaults to True.
            Returns:
                Whatever is returned by the super()._post() method.
            """
        for record in self:
            if (record.move_type == 'entry' and record.currency_id.id == record.company_currency_id.id) or self._context.get('skip_currency_rate_validation',False):
                continue
            rates = record.currency_id._get_rates(
                        record.company_id, record.date)
            rate = 1 / rates.get(record.currency_id.id,1)
            # print(" -------------- ", (rate / record.currency_rate))
            # print(" -------------- ", (record.currency_rate / rate) )
            if not record.currency_rate or record.currency_rate == 0:
                continue
            if rate > record.currency_rate and (rate / record.currency_rate) > 1.1:
                raise ValidationError(
                    _("The currency rate must be less than (10%) difference, please check it."))
            elif rate < record.currency_rate and (record.currency_rate / rate) > 1.1:
                raise ValidationError(
                    _("The currency rate must be less than (10%) difference, please check it."))

        posted : AccountMove = super()._post(soft=soft)
        for move in posted:
            move.confirmed_rate = move.currency_rate
        return posted

    # def write(self, vals):
    #     res = super().write(vals)
    #     for rec in self:
    #         if rec.move_type in ['out_invoice', 'in_invoice']:
    #             invoice_date = vals['invoice_date'] if 'invoice_date' in vals else rec.invoice_date
    #             currency_id = vals['currency_id'] if 'currency_id' in vals else rec.currency_id.id
    #             currency_rate = vals['currency_rate'] if 'currency_rate' in vals else rec.currency_rate
    #             invoice_line_ids = vals['invoice_line_ids'] if 'invoice_line_ids' in vals else rec.invoice_line_ids
    #             currency = rec.env['res.currency.rate'].search(
    #                 [('currency_id', '=', currency_id), ('name', '=', invoice_date)])
    #             if not currency:
    #                 rec.env['res.currency.rate'].create({
    #                     'name': invoice_date,
    #                     'company_rate': (1 / currency_rate),
    #                     'currency_id': currency_id
    #                 })
    #             else:
    #                 currency.write({
    #                     'company_rate': (1 / currency_rate)})
    #             for invoice_line in rec.invoice_line_ids:
    #                     invoice_line.write({"currency_rate":(1 / currency_rate)})
    #     return res
    # @api.model_create_multi
    # def create(self, vals):
    #     result = super().create(vals)
    #     for res in result:
    #         for rec in self:
    #             if res['move_type'] in ['out_invoice', 'in_invoice']:
    #                 invoice_date = res['invoice_date'] if 'invoice_date' in res else rec.invoice_date
    #                 currency_id = res['currency_id'].id if 'currency_id' in res else rec.currency_id.id
    #                 currency_rate = res['currency_rate'] if 'currency_rate' in res else rec.currency_rate
    #                 currency = rec.env['res.currency.rate'].search(
    #                     [('currency_id', '=', currency_id), ('name', '=', invoice_date)])
    #                 if not currency:
    #                     rec.env['res.currency.rate'].create({
    #                         'name': invoice_date,
    #                         'company_rate': (1 / currency_rate),
    #                         'currency_id': currency_id
    #                     })
    #                 else:
    #                     currency.write({
    #                         'company_rate': (1 / currency_rate)})
    #     return result


# class PurchaseOrder(models.Model):
#     _inherit = 'purchase.order'

#     def _prepare_invoice(self):
#         vals = super()._prepare_invoice()
#         vals["currency_rate"] = self.currency_id.inverse_rate
#         return vals

# class SaleeOrder(models.Model):
#     _inherit = 'sale.order'

#     def _prepare_invoice(self):
#         vals = super()._prepare_invoice()
#         vals["currency_rate"] = self.currency_id.inverse_rate
#         return vals
