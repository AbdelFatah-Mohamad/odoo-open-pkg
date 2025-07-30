{
    'name': 'AlshayebCo Odoo Check Management',
    'version': '18.0',
    'summary': 'Check Management',
    'category': 'AlshayebCo',
    'author': 'Abdelfatah Mohammad (AlshayebCo)',
    'website': 'https://alshayebco.odoo.com/',
    'maintainer': 'Abdelfatah Mohamad',
    # 'license': '',
    'depends': [
        'account_accountant','website'
    ],
    "data": [
        "data/data.xml",
        "data/sequence.xml",
        "data/cheque_attribute_data.xml",
        "security/ir.model.access.csv",
        "wizard/account_check_bank_transfer.xml",
        "wizard/account_check_cancel_reason.xml",
        "wizard/account_check_return_check.xml",
        "wizard/checks_issued_bank_wizard.xml",
        "wizard/invoice_print_cheque_transient_views.xml",
        "wizard/account_check_bank_transfer.xml",
        "wizard/account_check_cancel_reason.xml",
        "wizard/account_check_return_check.xml",
        "wizard/checks_issued_bank_wizard.xml",
        "wizard/invoice_print_cheque_transient_views.xml",
        "reports/account_move_line_report.xml",
        "reports/cheque_report.xml",
        "reports/payment_receipt_document.xml",


        "views/account_check_book_views.xml",
        "views/account_check_tag_views.xml",
        "views/account_check_view.xml",
        "views/account_journal.xml",
        "views/account_move_line.xml",
        "views/account_move_view.xml",
        "views/account_payment_view.xml",
        "views/bank_view.xml",
        "views/website_template_view.xml",
        "views/menus.xml",
    ],
    'assets': {
        'web.assets_frontend': [
            'odoo_check_managment/static/src/js/jquery_Jcrop.js',
            'odoo_check_managment/static/src/js/bank_check.js'
        ],
        'web.assets_backend': [
            'odoo_check_managment/static/src/components/**/*',
        ],
    },
    'excludes':['odoo_cheque_management'],
    'installable': True,
    'auto_install': False,
    'application': False,
}
