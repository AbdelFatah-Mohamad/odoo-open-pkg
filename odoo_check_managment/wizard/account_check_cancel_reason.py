# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AccountCheckCancelReason(models.TransientModel):
    _name = 'account.check.cancel.reason'
    _description = _('AccountCheckCancelReason')

    check_id = fields.Many2one("account.check",)
    name = fields.Char(_('Cancel Reason'))

    def add(self):
        self.check_id.write({"state": "cancelled", "cancel_reason": self.name})