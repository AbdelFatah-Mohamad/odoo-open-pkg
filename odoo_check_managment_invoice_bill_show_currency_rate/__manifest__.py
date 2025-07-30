# -*- coding: utf-8 -*-
{
    'name': 'Odoo check managment invoice bill show currency_rate',
    'version': '18.0',
    'summary': """ Odoo check managment invoice bill show currency_rat Summary """,
    'author': 'Abdelfatah Mohammad (AlshayebCo)',
    'website': 'https://alshayebco.odoo.com/',
    'category': 'AlshayebCo',
    'depends': ['odoo_check_managment', 'invoice_bill_show_currency_rate'],
    "data": [
        "views/account_payment_views.xml"
    ],
    'application': False,
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
