# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    so_currency_rate = fields.Float(
        string="Currency Rate", store=True, readonly=False,compute="_compute_so_currency_rate")
    
    @api.onchange('pricelist_id')
    @api.depends("pricelist_id.currency_id","pricelist_id")
    def _compute_so_currency_rate(self):
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
            if record.pricelist_id:
                # print(" -------------- ",
                #       record.currency_id._get_rates(record.company_id, record.invoice_date))
                # print(" -------------- ",1/rates.get(record.currency_id._get_rates(record.company_id, record.invoice_date)))
                # if not record.confirmed_rate or record.confirmed_rate == '':
                rates = record.pricelist_id.currency_id._get_rates(
                    record.company_id, record.date_order)
                record.so_currency_rate = 1 / rates.get(record.pricelist_id.currency_id.id)
            else:
                record.so_currency_rate = 1

    def _prepare_invoice(self):
        """
        Prepare the dict of values to create the new invoice for a sales order. This method may be
        overridden to implement custom invoice generation (making sure to call super() to establish
        a clean extension chain).
        """
        self.ensure_one()

        values = {
            'ref': self.client_order_ref or '',
            'move_type': 'out_invoice',
            'narration': self.note,
            'currency_id': self.currency_id.id,
            'campaign_id': self.campaign_id.id,
            'medium_id': self.medium_id.id,
            'source_id': self.source_id.id,
            'team_id': self.team_id.id,
            'currency_rate': self.so_currency_rate,
            'partner_id': self.partner_invoice_id.id,
            'partner_shipping_id': self.partner_shipping_id.id,
            'fiscal_position_id': (self.fiscal_position_id or self.fiscal_position_id._get_fiscal_position(self.partner_invoice_id)).id,
            'invoice_origin': self.name,
            'invoice_payment_term_id': self.payment_term_id.id,
            'invoice_user_id': self.user_id.id,
            'payment_reference': self.reference,
            'transaction_ids': [Command.set(self.transaction_ids.ids)],
            'company_id': self.company_id.id,
            'invoice_line_ids': [],
            'user_id': self.user_id.id,
        }
        if self.journal_id:
            values['journal_id'] = self.journal_id.id
        return values

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _prepare_invoice_line(self, **optional_values):
        """Prepare the values to create the new invoice line for a sales order line.

        :param optional_values: any parameter that should be added to the returned invoice line
        :rtype: dict
        """
        self.ensure_one()
        res = {
            'display_type': self.display_type or 'product',
            'sequence': self.sequence,
            'name': self.name,
            'product_id': self.product_id.id,
            'product_uom_id': self.product_uom.id,
            'quantity': self.qty_to_invoice,
            'discount': self.discount,
            'price_unit': self.price_unit,
            'tax_ids': [Command.set(self.tax_id.ids)],
            'sale_line_ids': [Command.link(self.id)],
            'is_downpayment': self.is_downpayment,
        }
        self._set_analytic_distribution(res, **optional_values)
        if optional_values:
            res.update(optional_values)
        if self.display_type:
            res['account_id'] = False
        return res
