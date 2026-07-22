# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.tests import TransactionCase, tagged
from odoo.exceptions import UserError


@tagged('post_install', '-at_install')
class TestComplaints(TransactionCase):
    """Test complaint workflow from submission to payment."""

    def setUp(self):
        super().setUp()

        # Create groups
        self.group_user = self.env.ref('complaints_mgmt.group_complaint_user')
        self.group_company = self.env.ref('complaints_mgmt.group_complaint_company')
        self.group_admin = self.env.ref('complaints_mgmt.group_complaint_admin')
        self.group_internal = self.env.ref('base.group_user')

        # Create test users
        self.user_complainant = self.env['res.users'].create({
            'name': 'Test Complainant',
            'login': 'complainant@test.com',
            'email': 'complainant@test.com',
            'groups_id': [(6, 0, [self.group_user.id, self.group_internal.id])],
        })

        self.user_company = self.env['res.users'].create({
            'name': 'Company Reviewer',
            'login': 'company@test.com',
            'email': 'company@test.com',
            'groups_id': [(6, 0, [self.group_company.id, self.group_internal.id])],
        })

        # Create target company; enroll complainant + reviewer
        self.target_company = self.env['complaints.target_company'].create({
            'name': 'Test Target Company',
            'complainant_user_ids': [(6, 0, [self.user_complainant.id])],
            'reviewer_user_ids': [(6, 0, [self.user_company.id])],
        })

        self.user_admin = self.env['res.users'].create({
            'name': 'Admin User',
            'login': 'admin@test.com',
            'email': 'admin@test.com',
            'groups_id': [(6, 0, [self.group_admin.id, self.group_internal.id])],
        })

        # Create test currency
        self.currency = self.env['res.currency'].search([('name', '=', 'USD')], limit=1)
        if not self.currency:
            self.currency = self.env.company.currency_id

    def test_flow_submit_to_paid(self):
        """Test complete complaint workflow: new → in_progress → approved → in_payment → paid."""

        # Step 1: User creates complaint
        complaint = self.env['complaints.complaint'].with_user(self.user_complainant).create({
            'partner_id': self.user_complainant.partner_id.id,
            'target_company_id': self.target_company.id,
            'description': 'Test complaint description',
            'amount_requested': 1000.0,
            'currency_id': self.currency.id,
        })

        self.assertEqual(complaint.state, 'new', 'New complaint should be in "new" state')
        self.assertTrue(complaint.name.startswith('COMP'), 'Complaint reference should start with COMP')

        # Step 2: Admin sets to In Progress
        complaint.with_user(self.user_admin).action_set_in_progress()
        self.assertEqual(complaint.state, 'in_progress', 'Complaint should be in "in_progress" state')

        # Step 3: Company user approves
        complaint.with_user(self.user_company).write({
            'amount_covered_company': 800.0,
            'company_notes': 'Approved with partial coverage',
        })
        complaint.with_user(self.user_company).action_approve()
        self.assertEqual(complaint.state, 'approved', 'Complaint should be in "approved" state')

        # Step 4: Admin sets to In Payment
        complaint.with_user(self.user_admin).write({
            'amount_approved_admin': 800.0,
        })
        complaint.with_user(self.user_admin).action_set_in_payment()
        self.assertEqual(complaint.state, 'in_payment', 'Complaint should be in "in_payment" state')

        # Step 5: Admin marks as Paid
        complaint.with_user(self.user_admin).action_set_paid()
        self.assertEqual(complaint.state, 'paid', 'Complaint should be in "paid" state')

    def test_flow_submit_to_rejected(self):
        """Test rejection workflow: new → in_progress → rejected."""

        # Step 1: User creates complaint
        complaint = self.env['complaints.complaint'].with_user(self.user_complainant).create({
            'partner_id': self.user_complainant.partner_id.id,
            'target_company_id': self.target_company.id,
            'description': 'Test complaint for rejection',
            'amount_requested': 500.0,
            'currency_id': self.currency.id,
        })

        self.assertEqual(complaint.state, 'new')

        # Step 2: Admin sets to In Progress
        complaint.with_user(self.user_admin).action_set_in_progress()
        self.assertEqual(complaint.state, 'in_progress')

        # Step 3: Company user rejects
        complaint.with_user(self.user_company).write({
            'company_notes': 'Complaint does not meet criteria',
        })
        complaint.with_user(self.user_company).action_reject()
        self.assertEqual(complaint.state, 'rejected', 'Complaint should be in "rejected" state')

    def test_request_documents(self):
        """Test company requesting additional documents."""

        complaint = self.env['complaints.complaint'].with_user(self.user_complainant).create({
            'partner_id': self.user_complainant.partner_id.id,
            'target_company_id': self.target_company.id,
            'description': 'Test complaint for doc request',
            'amount_requested': 300.0,
            'currency_id': self.currency.id,
        })

        # Admin sets to In Progress
        complaint.with_user(self.user_admin).action_set_in_progress()

        # Company requests documents
        complaint.with_user(self.user_company).action_request_docs()

        # Check that activity was created
        activities = self.env['mail.activity'].search([
            ('res_id', '=', complaint.id),
            ('res_model', '=', 'complaints.complaint'),
        ])
        self.assertTrue(activities, 'Activity should be created for document request')

    def test_child_partner_constraint(self):
        """Test constraint: child_partner_id must be child of partner_id."""

        # Create parent and unrelated partner
        parent = self.user_complainant.partner_id
        unrelated = self.env['res.partner'].create({
            'name': 'Unrelated Person',
            'type': 'contact',
        })

        # Try to create complaint with unrelated child (should fail)
        with self.assertRaises(UserError, msg='Should raise error for unrelated child'):
            self.env['complaints.complaint'].with_user(self.user_complainant).create({
                'partner_id': parent.id,
                'child_partner_id': unrelated.id,
                'target_company_id': self.target_company.id,
                'description': 'Invalid child test',
                'amount_requested': 100.0,
                'currency_id': self.currency.id,
            })

    def test_security_user_sees_own_only(self):
        """Test that regular users only see their own complaints."""

        # Create complaint as complainant
        complaint = self.env['complaints.complaint'].with_user(self.user_complainant).create({
            'partner_id': self.user_complainant.partner_id.id,
            'target_company_id': self.target_company.id,
            'description': 'Security test complaint',
            'amount_requested': 200.0,
            'currency_id': self.currency.id,
        })

        # Create another user
        other_user = self.env['res.users'].create({
            'name': 'Other User',
            'login': 'other@test.com',
            'email': 'other@test.com',
            'groups_id': [(6, 0, [self.group_user.id, self.group_internal.id])],
        })

        # Other user should not see the complaint
        complaints_visible = self.env['complaints.complaint'].with_user(other_user).search([
            ('id', '=', complaint.id)
        ])
        self.assertEqual(len(complaints_visible), 0, 'Other user should not see complaint')

        # Original user should see their complaint
        complaints_visible = self.env['complaints.complaint'].with_user(self.user_complainant).search([
            ('id', '=', complaint.id)
        ])
        self.assertEqual(len(complaints_visible), 1, 'Complainant should see their own complaint')

    def test_admin_permission_check(self):
        """Test that only admins can perform admin actions."""

        complaint = self.env['complaints.complaint'].with_user(self.user_complainant).create({
            'partner_id': self.user_complainant.partner_id.id,
            'target_company_id': self.target_company.id,
            'description': 'Admin permission test',
            'amount_requested': 150.0,
            'currency_id': self.currency.id,
        })

        # Regular user should not be able to set in_progress
        with self.assertRaises(UserError, msg='Non-admin should not be able to set in_progress'):
            complaint.with_user(self.user_complainant).action_set_in_progress()

        # Admin should be able to
        complaint.with_user(self.user_admin).action_set_in_progress()
        self.assertEqual(complaint.state, 'in_progress')
