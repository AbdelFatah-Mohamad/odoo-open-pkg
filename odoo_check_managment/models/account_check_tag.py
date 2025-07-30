# -*- coding: utf-8 -*-
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AccountCheckTag(models.Model):
    _name = 'account.check.tag'
    _description = 'AccountCheckTag'

    name = fields.Char('Name')

    color = fields.Integer('Color Index', default=0)
    sequence = fields.Integer('Sequence', default=10)
    active = fields.Boolean('Active', default=True)