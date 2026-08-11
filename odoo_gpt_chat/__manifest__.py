# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

#################################################################################
# Author      : Terabits Technolab (<https://www.terabits.xyz>)
# Copyright(c): 2021-25
# All Rights Reserved.
#
# This module is copyright property of the author mentioned above.
# You can't redistribute/reshare/recreate it for any purpose.
#
#################################################################################

{
    'name': 'AI Chatbot | Lead Generation Chatbot | ChatGPT Live chat Support with your own data | AI powered chatbot with custom knowledge',
    'summary': """Let an AI chatbot handle the first round of customer questions and lead capture, day or night. It learns from your own website content and knowledge base, drops easily onto your site, and answers visitors instantly. You can shape how it behaves and check the analytics to see what people are asking.
AI Chatbot, Website Chatbot, Lead Generation, Customer Support Chatbot, AI Support Agent, Knowledge Base Chatbot, Custom AI Chatbot, Website Assistant, Live Chat, AI Powered Chatbot, Chatbot Integration""",
    'version': '19.0.1.1.2',
    'category': 'Website',
    'sequence': 40,
    'author': 'Terabits Technolab',
    'license': 'OPL-1',
    'description': """Let an AI chatbot handle the first round of customer questions and lead capture, day or night. It learns from your own website content and knowledge base, drops easily onto your site, and answers visitors instantly. You can shape how it behaves and check the analytics to see what people are asking.""",
    
    
    "price": "00.0",
    "currency": "USD",
    'depends': ['base', 'base_setup', 'web', 'crm'],
    'data': [
        'security/res_groups.xml',
        'datas/user.xml',
	    'views/res_users_views.xml',
        'views/settings_whisper_patch.xml',
        'views/crm_lead_view.xml',
    ],
    'installable': True,
    'application': True,
    'website': 'https://www.whisperchat.ai',
    'images': ['static/description/banner.gif'],
    'live_test_url': 'https://www.whisperchat.ai/demo',
     'assets': {
        'web.assets_frontend': [
            '/odoo_gpt_chat/static/src/notificationPatch.js',
            '/odoo_gpt_chat/static/src/notificationPatch.scss',
        ],
        'web.assets_backend': [
            '/odoo_gpt_chat/static/src/notificationPatch.js',
            '/odoo_gpt_chat/static/src/notificationPatch.scss',
        ]
     }
}
