# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AccountPaymentMethod(models.Model):
    _inherit = 'account.payment.method'

    @api.model
    def _get_payment_method_information(self):
        res = super()._get_payment_method_information()
        res['checks_in'] = {'mode': 'multi', 'type': ('bank',)}
        res['checks_out'] = {'mode': 'multi', 'type': ('bank',)}
        return res