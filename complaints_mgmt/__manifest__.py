# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Complaints Management',
    'version': '18.0.1.0.0',
    'category': 'Services',
    'summary': 'Manage user complaints against companies with multi-role workflow',
    'description': """
Complaints Management System
=============================

Features:
---------
* User complaint submission against companies
* Three-tier role system: User, Company User, Admin
* Complete lifecycle: New → In Progress → Approved/Rejected → In Payment → PAID
* Multi-currency support for monetary fields
* Child contact support for complaints on behalf of minors
* Document attachment management
* Activity-based notifications and chatter integration
* Company reviewer workflow (approve/reject/request docs)
* Admin payment workflow
* Security: record rules ensure proper access control
    """,
    'author': 'AlshayebCo',
    'website': 'https://www.alshayebco.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
    ],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'security/rules.xml',
        'data/sequence.xml',
        'data/demo_target_company.xml',
        'views/menu.xml',
        'views/target_company_views.xml',
        'views/complaint_views.xml',
        'data/mail_templates.xml',
    ],
    'demo': [
        # 'data/demo.xml',  # Optional demo data
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
