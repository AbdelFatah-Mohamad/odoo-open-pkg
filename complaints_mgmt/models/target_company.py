# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ComplaintTargetCompany(models.Model):
    """Configuration model for companies that can be complained against."""

    _name = 'complaints.target_company'
    _description = 'Target Company Configuration'
    _order = 'name'

    name = fields.Char(
        string='Company Name',
        required=True,
        help='Name of the company that can be complained against'
    )

    active = fields.Boolean(
        default=True,
        help='Uncheck to disable this target company'
    )

    reviewer_user_ids = fields.Many2many(
        'res.users',
        'target_company_reviewer_rel',
        'company_id',
        'user_id',
        string='Reviewer Users',
        help='Users who can review and act on complaints for this company'
    )

    complainant_user_ids = fields.Many2many(
        'res.users',
        'target_company_complainant_rel',
        'company_id',
        'user_id',
        string='Complainant Users',
        help='Users who are allowed to submit complaints against this company'
    )

    complaint_count = fields.Integer(
        string='Complaints',
        compute='_compute_complaint_count',
        help='Number of complaints filed against this company'
    )

    def _compute_complaint_count(self):
        """Compute the number of complaints for this target company."""
        for rec in self:
            rec.complaint_count = self.env['complaints.complaint'].search_count([
                ('target_company_id', '=', rec.id)
            ])

    def action_view_complaints(self):
        """Smart button action to view all complaints for this company."""
        self.ensure_one()
        return {
            'name': f'Complaints - {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'complaints.complaint',
            'view_mode': 'kanban,list,form',
            'domain': [('target_company_id', '=', self.id)],
            'context': {'default_target_company_id': self.id},
        }

    @api.model
    def _name_search(self, name='', domain=None, operator='ilike', limit=None, order=None):
        """Override name_search to filter by user permissions when called from complaint form."""
        domain = list(domain or [])

        # Check if we're being called from a complaint context
        # If so, filter by user's allowed companies
        user = self.env.user

        # Always filter for active companies
        domain.append(('active', '=', True))

        # Apply user-based filtering
        if not user.has_group('complaints_mgmt.group_complaint_admin'):
            # Non-admin users only see companies they can complain against
            domain.append(('complainant_user_ids', 'in', [user.id]))

        return super()._name_search(name=name, domain=domain, operator=operator, limit=limit, order=order)
