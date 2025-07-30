# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import date
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, get_lang
from odoo.tools.float_utils import float_compare, float_round
DEFAULT_SERVER_DATE_FORMAT = "%Y-%m-%d"
DEFAULT_SERVER_TIME_FORMAT = "%H:%M:%S"
DEFAULT_SERVER_DATETIME_FORMAT = "%s %s" % (
    DEFAULT_SERVER_DATE_FORMAT,
    DEFAULT_SERVER_TIME_FORMAT)

_logger = logging.getLogger(__name__)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    currency_rate = fields.Float("Currency Rate", compute='_compute_currency_rate', store=True, readonly=True, help='Ratio between the purchase order currency and the company currency')

    @api.depends("po_currency_rate")
    def _compute_currency_rate(self):
        for rec in self:
            rec.currency_rate = 1 / rec.po_currency_rate

    po_currency_rate = fields.Float( default="1",
        string="Currency Rate", store=True,precompute=True,compute_sudo=True, readonly=False,compute="_compute_po_currency_rate")
    
    @api.onchange('currency_id')
    @api.depends("currency_id")
    def _compute_po_currency_rate(self):
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
            print('****************************** ',record.po_currency_rate)
            if record.currency_id:

                # print(" -------------- ",
                #       record.currency_id._get_rates(record.company_id, record.invoice_date))
                # print(" -------------- ",1/rates.get(record.currency_id._get_rates(record.company_id, record.invoice_date)))
                # if not record.confirmed_rate or record.confirmed_rate == '':
                rates = record.currency_id._get_rates(
                    record.company_id, date.today())
                record.po_currency_rate = 1 / rates.get(record.currency_id.id)
            else:
                record.po_currency_rate = 1

    def _prepare_invoice(self):
        """Prepare the dict of values to create the new invoice for a purchase order.
        """
        self.ensure_one()
        move_type = self._context.get('default_move_type', 'in_invoice')

        partner_invoice = self.env['res.partner'].browse(self.partner_id.address_get(['invoice'])['invoice'])
        partner_bank_id = self.partner_id.commercial_partner_id.bank_ids.filtered_domain(['|', ('company_id', '=', False), ('company_id', '=', self.company_id.id)])[:1]

        invoice_vals = {
            'ref': self.partner_ref or '',
            'move_type': move_type,
            'narration': self.notes,
            'currency_id': self.currency_id.id,
            'partner_id': partner_invoice.id,
            'fiscal_position_id': (self.fiscal_position_id or self.fiscal_position_id._get_fiscal_position(partner_invoice)).id,
            'payment_reference': self.partner_ref or '',
            'partner_bank_id': partner_bank_id.id,
            'invoice_origin': self.name,
            'currency_rate': self.po_currency_rate,
            'invoice_payment_term_id': self.payment_term_id.id,
            'invoice_line_ids': [],
            'company_id': self.company_id.id,
        }
        return invoice_vals



class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'
    
    currency_rate = fields.Float(related='order_id.currency_rate')
    # is_first_change = fields.Boolean(default=True)
    # price_unit = fields.Float(
    #     string='Unit Price', required=True, digits='Product Price',
    #     compute='_compute_price_unit_and_date_planned_and_name', readonly=False, store=True)

    @api.depends('product_qty', 'product_uom', 'company_id')
    def _compute_price_unit_and_date_planned_and_name(self):
        for line in self:
            if not line.product_id or line.invoice_lines or not line.company_id:
                continue
            params = line._get_select_sellers_params()
            seller = line.product_id._select_seller(
                partner_id=line.partner_id,
                quantity=line.product_qty,
                date=line.order_id.date_order and line.order_id.date_order.date() or fields.Date.context_today(line),
                uom_id=line.product_uom,
                params=params)
            if seller or not line.date_planned:
                line.date_planned = line._get_date_planned(seller).strftime(DEFAULT_SERVER_DATETIME_FORMAT)
            # If not seller, use the standard price. It needs a proper currency conversion.
            if not seller:
                line.discount = 0
                unavailable_seller = line.product_id.seller_ids.filtered(
                    lambda s: s.partner_id == line.order_id.partner_id)
                if not unavailable_seller and line.price_unit and line.product_uom == line._origin.product_uom:
                    # Avoid to modify the price unit if there is no price list for this partner and
                    # the line has already one to avoid to override unit price set manually.
                    continue
                po_line_uom = line.product_uom or line.product_id.uom_po_id
                price_unit = line.env['account.tax']._fix_tax_included_price_company(
                    line.product_id.uom_id._compute_price(line.product_id.standard_price, po_line_uom),
                    line.product_id.supplier_taxes_id,
                    line.taxes_id,
                    line.company_id,
                )
                price_unit = price_unit * line.currency_rate
                if line.price_unit == 0:
                    line.price_unit = float_round(price_unit, precision_digits=max(line.currency_id.decimal_places, self.env['decimal.precision'].precision_get('Product Price')))
                else:
                    line.price_unit = line.price_unit
                continue
            price_unit = line.env['account.tax']._fix_tax_included_price_company(seller.price, line.product_id.supplier_taxes_id, line.taxes_id, line.company_id) if seller else 0.0
            price_unit = seller.currency_id._convert(price_unit, line.currency_id, line.company_id, line.date_order or fields.Date.context_today(line), False)
            price_unit = float_round(price_unit, precision_digits=max(line.currency_id.decimal_places, self.env['decimal.precision'].precision_get('Product Price')))
            line.price_unit = seller.product_uom._compute_price(price_unit, line.product_uom)
            line.discount = seller.discount or 0.0
            line.is_first_change = False
            # record product names to avoid resetting custom descriptions
            default_names = []
            vendors = line.product_id._prepare_sellers({})
            product_ctx = {'seller_id': None, 'partner_id': None, 'lang': get_lang(line.env, line.partner_id.lang).code}
            default_names.append(line._get_product_purchase_description(line.product_id.with_context(product_ctx)))
            for vendor in vendors:
                product_ctx = {'seller_id': vendor.id, 'lang': get_lang(line.env, line.partner_id.lang).code}
                default_names.append(line._get_product_purchase_description(line.product_id.with_context(product_ctx)))
            if not line.name or line.name in default_names:
                product_ctx = {'seller_id': seller.id, 'lang': get_lang(line.env, line.partner_id.lang).code}
                line.name = line._get_product_purchase_description(line.product_id.with_context(product_ctx))
