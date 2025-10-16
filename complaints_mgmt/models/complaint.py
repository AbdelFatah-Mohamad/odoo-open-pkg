# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class Complaint(models.Model):
    """Main complaint model with full lifecycle management."""

    _name = 'complaints.complaint'
    _description = 'Complaint'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    # Basic Information
    name = fields.Char(
        string='Complaint Reference',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
        tracking=True
    )

    partner_id = fields.Many2one(
        'res.partner',
        string='Complainant',
        required=True,
        readonly=True,
        default=lambda self: self.env.user.partner_id.id,
        tracking=True,
        help='The person submitting the complaint (defaults to current user)'
    )

    child_partner_id = fields.Many2one(
        'res.partner',
        string='Child (if applicable)',
        domain="[('parent_id', '=', partner_id), ('type', '=', 'contact')]",
        tracking=True,
        help='Select a child contact if this complaint is on their behalf'
    )

    target_company_id = fields.Many2one(
        'complaints.target_company',
        string='Target Company',
        required=True,
        tracking=True,
        help='The company this complaint is directed against'
    )

    allowed_target_company_ids = fields.Many2many(
        'complaints.target_company',
        compute='_compute_allowed_targets',
        store=False,
        string='Allowed Target Companies',
        help='Companies the current user is allowed to complain against'
    )

    description = fields.Text(
        string='Complaint Description',
        required=True,
        tracking=True,
        help='Detailed description of the complaint'
    )

    attachment_ids = fields.Many2many(
        'ir.attachment',
        'complaint_attachment_rel',
        'complaint_id',
        'attachment_id',
        string='Attachments',
        help='Supporting documents for this complaint'
    )

    # Monetary Fields
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        required=True,
        default=lambda self: self.env.company.currency_id.id,
        tracking=True
    )

    amount_requested = fields.Monetary(
        string='Amount Requested',
        currency_field='currency_id',
        tracking=True,
        help='Amount requested by the complainant'
    )

    amount_approved_admin = fields.Monetary(
        string='Amount Approved by Admin',
        currency_field='currency_id',
        groups='complaints_mgmt.group_complaint_admin',
        tracking=True,
        help='Amount approved by administrator'
    )

    amount_covered_company = fields.Monetary(
        string='Amount Covered by Company',
        currency_field='currency_id',
        groups='complaints_mgmt.group_complaint_company,complaints_mgmt.group_complaint_admin',
        tracking=True,
        help='Amount the company agrees to cover'
    )

    company_notes = fields.Text(
        string='Company Notes',
        groups='complaints_mgmt.group_complaint_company,complaints_mgmt.group_complaint_admin',
        tracking=True,
        help='Internal notes from the company reviewer'
    )

    # State Management
    state = fields.Selection(
        [
            ('new', 'New'),
            ('in_progress', 'In Progress'),
            ('approved', 'Approved'),
            ('in_payment', 'In Payment'),
            ('paid', 'PAID'),
            ('rejected', 'Rejected'),
        ],
        string='Status',
        default='new',
        required=True,
        tracking=True,
        help='Current status of the complaint'
    )

    # Computed Fields for UI Control
    can_company_act = fields.Boolean(
        string='Company Can Act',
        compute='_compute_flags',
        help='Whether company user can perform actions'
    )

    can_user_upload = fields.Boolean(
        string='User Can Upload',
        compute='_compute_flags',
        help='Whether user can upload additional documents'
    )

    @api.depends('state')
    def _compute_flags(self):
        """Compute UI control flags based on state."""
        for rec in self:
            # Company can act when complaint is in_progress
            rec.can_company_act = (rec.state == 'in_progress')

            # User can upload in new and in_progress states
            rec.can_user_upload = (rec.state in ['new', 'in_progress'])

    @api.depends_context('uid')
    def _compute_allowed_targets(self):
        """Compute which target companies the current user can complain against."""
        user = self.env.user
        if user.has_group('complaints_mgmt.group_complaint_admin'):
            # Admins can see all active target companies
            allowed = self.env['complaints.target_company'].search([('active', '=', True)])
        else:
            # Regular users see only active companies they're allowed to complain against
            allowed = self.env['complaints.target_company'].search([
                ('complainant_user_ids', 'in', [user.id]),
                ('active', '=', True)
            ])
        for rec in self:
            rec.allowed_target_company_ids = [(6, 0, allowed.ids)]

    @api.constrains('child_partner_id', 'partner_id')
    def _check_child_partner(self):
        """Ensure child_partner_id is actually a child of partner_id."""
        for rec in self:
            if rec.child_partner_id:
                if rec.child_partner_id.parent_id != rec.partner_id:
                    raise UserError(_(
                        'The selected child contact must be a child of the complainant. '
                        'Parent: %s, Child: %s'
                    ) % (rec.partner_id.name, rec.child_partner_id.name))

    @api.constrains('target_company_id')
    def _check_user_allowed_for_target(self):
        """Ensure user is allowed to file complaints against the target company."""
        for rec in self:
            # Skip check for admins
            if self.env.user.has_group('complaints_mgmt.group_complaint_admin'):
                continue

            # Check if user is in the complainant_user_ids
            if rec.target_company_id and self.env.user.id not in rec.target_company_id.complainant_user_ids.ids:
                raise UserError(_(
                    'You are not authorized to file a complaint against %s.'
                ) % rec.target_company_id.name)

    @api.model
    def default_get(self, fields_list):
        """Auto-default target_company_id if user has exactly one allowed company."""
        vals = super().default_get(fields_list)

        # Skip for admins - they can choose any company
        if self.env.user.has_group('complaints_mgmt.group_complaint_admin'):
            return vals

        # Find allowed active target companies for current user
        allowed = self.env['complaints.target_company'].search([
            ('complainant_user_ids', 'in', [self.env.user.id]),
            ('active', '=', True)
        ])

        # Auto-select if exactly one company is allowed
        if len(allowed) == 1:
            vals['target_company_id'] = allowed.id

        return vals

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to generate sequence for complaint reference."""
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('complaints.complaint') or _('New')
        return super().create(vals_list)

    # Helper Methods
    def _ensure_admin(self):
        """Raise error if current user is not an admin."""
        if not self.env.user.has_group('complaints_mgmt.group_complaint_admin'):
            raise UserError(_('Only administrators can perform this action.'))

    def _ensure_company(self):
        """Raise error if current user is not a company user."""
        if not self.env.user.has_group('complaints_mgmt.group_complaint_company'):
            raise UserError(_('Only company users can perform this action.'))

    def _admin_partners(self):
        """Return partner recordset of all admin users."""
        admin_group = self.env.ref('complaints_mgmt.group_complaint_admin')
        admin_users = admin_group.users
        return admin_users.mapped('partner_id')

    def _company_user_id(self):
        """Find reviewer user for the target company, return user ID."""
        self.ensure_one()
        # Get reviewer users from target company configuration
        if self.target_company_id and self.target_company_id.reviewer_user_ids:
            return self.target_company_id.reviewer_user_ids[0].id
        # Fallback to current user if no reviewers configured
        return self.env.user.id

    def _user_partner_responsible(self):
        """Return the complainant's partner ID."""
        self.ensure_one()
        return self.partner_id.id

    def _notify_admin_and_user(self, subject):
        """Post message to complainant and all admins."""
        self.ensure_one()
        partners = self._admin_partners() | self.partner_id
        self.message_post(
            body=subject,
            subject=subject,
            partner_ids=partners.ids,
            message_type='notification',
            subtype_xmlid='mail.mt_comment',
        )

    # Admin Actions
    def action_set_in_progress(self):
        """Admin sets complaint to In Progress and notifies company."""
        self._ensure_admin()
        for rec in self:
            rec.write({'state': 'in_progress'})
            rec.message_post(body=_('Complaint moved to In Progress by administrator.'))

            # Schedule activity for company user
            company_user = rec._company_user_id()
            if company_user:
                rec.activity_schedule(
                    'mail.mail_activity_data_todo',
                    user_id=company_user,
                    summary=_('Review complaint: %s') % rec.name,
                    note=_('Please review this complaint and take appropriate action.')
                )

    def action_set_in_payment(self):
        """Admin sets approved complaint to In Payment."""
        self._ensure_admin()
        for rec in self:
            if rec.state != 'approved':
                raise UserError(_('Only approved complaints can be set to In Payment.'))
            rec.write({'state': 'in_payment'})
            rec._notify_admin_and_user(_('Complaint %s is now In Payment.') % rec.name)

    def action_set_paid(self):
        """Admin marks complaint as PAID (terminal state)."""
        self._ensure_admin()
        for rec in self:
            if rec.state != 'in_payment':
                raise UserError(_('Only complaints in In Payment state can be marked as Paid.'))
            rec.write({'state': 'paid'})
            rec._notify_admin_and_user(_('Complaint %s has been marked as PAID. Case closed.') % rec.name)

    # Company Actions
    def action_request_docs(self):
        """Company user requests additional documents from complainant."""
        self._ensure_company()
        for rec in self:
            if rec.state != 'in_progress':
                raise UserError(_('Can only request documents when complaint is In Progress.'))

            rec.message_post(body=_('Company has requested additional documents.'))

            # Schedule activity for user
            rec.activity_schedule(
                'mail.mail_activity_data_upload_file',
                user_id=rec.partner_id.user_ids[0].id if rec.partner_id.user_ids else self.env.uid,
                summary=_('Upload additional documents for complaint: %s') % rec.name,
                note=_('The company has requested additional supporting documents.')
            )

    def action_approve(self):
        """Company user approves the complaint."""
        self._ensure_company()
        for rec in self:
            if rec.state != 'in_progress':
                raise UserError(_('Can only approve complaints that are In Progress.'))

            rec.write({'state': 'approved'})
            rec._notify_admin_and_user(_('Complaint %s has been APPROVED by the company.') % rec.name)

    def action_reject(self):
        """Company user rejects the complaint (terminal state)."""
        self._ensure_company()
        for rec in self:
            if rec.state != 'in_progress':
                raise UserError(_('Can only reject complaints that are In Progress.'))

            rec.write({'state': 'rejected'})
            rec._notify_admin_and_user(_('Complaint %s has been REJECTED by the company.') % rec.name)
