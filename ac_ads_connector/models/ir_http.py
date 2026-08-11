# -*- coding: utf-8 -*-
from odoo import models
from odoo.http import Response, request

#: ("URL_PARAMETER", "COOKIE_NAME") — deliberately NOT added to
#: utm.mixin.tracking_fields(): consumers like link_tracker.create() force-set
#: every tracking field on every utm model and would crash on models without a
#: gclid column. Click-id capture therefore lives in its own hook, and only
#: crm.lead reads the cookies back (models/crm_lead.py default_get).
CLICK_ID_PARAMS = [
    ("gclid", "odoo_gclid"),
    ("fbclid", "odoo_fbclid"),
]


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _post_dispatch(cls, response):
        """Capture ad-platform click IDs (gclid/fbclid) as 31-day optional
        cookies — no Odoo module does this natively (verified core+enterprise).
        Mirrors utm's _set_utm mechanics, including the consent gating that
        `cookie_type='optional'` implies when a cookies bar is active."""
        odoo_response = Response.load(response)
        domain = cls.get_utm_domain_cookies() if request else None
        for url_parameter, cookie_name in CLICK_ID_PARAMS:
            if (request and url_parameter in request.params
                    and request.cookies.get(cookie_name)
                    != request.params[url_parameter]):
                odoo_response.set_cookie(
                    cookie_name, request.params[url_parameter],
                    max_age=31 * 24 * 3600, domain=domain,
                    cookie_type="optional")
        super()._post_dispatch(response)
