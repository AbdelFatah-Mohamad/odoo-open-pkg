{
    'name': 'Invoice/Bill Show Currency Rate',
    'version': '18.0',
    'summary': 'AlshayebCo',
    'category': 'AlshayebCo',
    'author': 'Abdelfatah Mohammad (AlshayebCo)',
    'website': 'https://alshayebco.odoo.com/',
    'maintainer': 'Abdelfatah Mohamad',
    'description': """Invoice/Bill Show Currency Rate""",
    'license': 'AGPL-3',
    'depends': ["account_accountant"
    # ,"stock_account","purchase_stock","sale_stock"
    ],
    "data": [
        "views/account_move.xml",
        "views/account_payment_views.xml",
        "views/account_move_views.xml",
        # "views/purchase_order_views.xml",
        # "views/sale_order_views.xml"
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
