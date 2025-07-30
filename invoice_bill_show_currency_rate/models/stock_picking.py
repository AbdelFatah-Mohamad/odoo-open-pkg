# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def x(self):
        self.button_validate()
        self.move_ids.action_get_account_moves()
        self.action_view_stock_valuation_layers()