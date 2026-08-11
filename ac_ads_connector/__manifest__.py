# -*- coding: utf-8 -*-
{
    'name': 'AC Ads Connector — Meta & Google Ads',
    'version': '19.0.2.0.0',
    'summary': "Pull campaigns, ad sets, ads, insights and leads from Meta & Google Ads into Odoo, "
               "manage them and feed conversions back",
    'description': """
Connect Odoo to Meta (Facebook/Instagram) Ads and Google Ads.

Key Features:

- Mirror the full campaign structure (campaigns, ad sets / ad groups, ads, creatives)
- Daily insight rows (spend, impressions, clicks, conversions) with dashboards and ROAS reporting
- Lead-form leads flow into CRM with UTM attribution and duplicate detection
- Pause/activate, edit budgets, and author campaigns from Odoo (created paused, never live implicitly)
- Custom audience creation with SHA-256 hashed member upload
- Won leads uploaded back to Google as click conversions
""",
    'category': 'AlshayebCo/Marketing',
    'author': 'AlShayeb Partners',
    'website': 'https://alshayeb.ps',
    'maintainer': 'AlShayeb Partners',
    'license': 'LGPL-3',
    'depends': ['crm', 'utm', 'mail', 'link_tracker', 'spreadsheet_dashboard'],
    # Odoo 19 checks these as PyPI distribution names (importlib.metadata), not import names.
    'external_dependencies': {'python': ['facebook-business', 'google-ads']},
    'data': [
        'security/ads_security.xml',
        'security/ir.model.access.csv',
        'data/utm_data.xml',
        'data/ir_cron_data.xml',
        'data/ads_dashboards.xml',
        'views/ads_account_views.xml',
        'views/ads_sync_log_views.xml',
        'views/ads_push_views.xml',
        'views/ads_campaign_views.xml',
        'views/ads_insight_views.xml',
        'views/ads_lead_views.xml',
        'views/ads_audience_views.xml',
        'views/ads_adset_views.xml',
        'views/ads_ad_views.xml',
        'views/ads_menus.xml',
        'report/ads_performance_report_views.xml',
    ],
    'application': True,
    'installable': True,
    'auto_install': False,
    'images': ['static/description/banner.png'],
}
