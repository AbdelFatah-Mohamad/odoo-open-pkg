# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ResCompanyComplaint(models.Model):
    """Configuration model to mark which companies handle complaints."""

    _name = 'res.company.complaint'
    _description = 'Company Complaint Configuration'

    name = fields.Char(string='Configuration Name', required=True)
    company_id = fields.Many2one('res.company', string='Company', required=True, ondelete='cascade')
    active = fields.Boolean(default=True, help='Set to false to disable complaint handling for this company')
