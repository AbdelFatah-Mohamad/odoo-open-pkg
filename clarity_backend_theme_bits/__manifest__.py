# -*- coding: utf-8 -*-
#################################################################################
# Author      : Terabits Technolab (<www.terabits.xyz>)
# Copyright(c): 2021-26
# All Rights Reserved.
#
# This module is copyright property of the author mentioned above.
# You can`t redistribute it and/or modify it.
#
#################################################################################

{
    "name": "Clarity Backend Theme for community",
    'summary': """Modern backend theme for Odoo providing responsive UI, sidebar navigation, app drawer, dark mode, multi-tab support, login redesign, list and kanban customization, and improved user experience.
Odoo backend theme, Odoo theme, responsive sidebar, dark mode, multi tab interface, UI customization, kanban view design, form view styling""",
    "version": "19.0.1.1.0",
    'author': "Terabits Technolab",
    'description': """Modern backend theme for Odoo providing responsive UI, sidebar navigation, app drawer, dark mode, multi-tab support, login redesign, list and kanban customization, and improved user experience.""",
    "sequence": 7,
    "license": "OPL-1",
    "category": "Themes/Backend",
    "website": "https://www.terabits.xyz/apps/19.0/clarity_backend_theme_bits",
    "depends": ["web"],
    "data": [ 
        'views/res_config_setting.xml',
        'views/res_users.xml',
        'views/webclient_templates.xml'
    ],
    "assets": {
        "web.assets_frontend": [
            'clarity_backend_theme_bits/static/src/scss/login.scss'
        ],
        "web.assets_backend": [   
            'clarity_backend_theme_bits/static/src/xml/WebClient.xml',
            'clarity_backend_theme_bits/static/src/xml/navbar/sidebar.xml', 
            'clarity_backend_theme_bits/static/src/xml/systray_items/user_menu.xml',
            'clarity_backend_theme_bits/static/src/js/SidebarBottom.js',  
            'clarity_backend_theme_bits/static/src/js/WebClient.js',
            'clarity_backend_theme_bits/static/src/scss/layout.scss',
            'clarity_backend_theme_bits/static/src/scss/navbar.scss', 
            'clarity_backend_theme_bits/static/src/js/navbar.js',  
        ],
    }, 
    'installable': True,
    'application': True,
    'auto_install': False,  
    'images': [
        'static/description/logo.gif',
        'static/description/theme_screenshot.gif',
    ],
}
