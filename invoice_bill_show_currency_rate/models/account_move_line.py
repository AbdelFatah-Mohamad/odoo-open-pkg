# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.depends('currency_id', 'company_id', 'move_id.date','move_id.currency_rate')
    def _compute_currency_rate(self):
        for line in self:
            if line.currency_id and line.move_id:
                if line.move_id.currency_rate not in [False, 0]:
                    line.currency_rate = 1/line.move_id.currency_rate
                else:
                    line.currency_rate = 1
            else:
                line.currency_rate = 1


