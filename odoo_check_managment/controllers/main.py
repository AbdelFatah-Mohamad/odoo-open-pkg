# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################

# 2 :  imports of odoo

import logging

from odoo import http, tools, _
from odoo.http import request
from odoo.addons.http_routing.models.ir_http import IrHttp

_logger = logging.getLogger(__name__)


class BankCheckManagement(http.Controller):
    @http.route('/bank/check/<model("res.bank"):bank_id>', type='http', auth="user", website=True)
    def res_bank_management(self, bank_id, **post):
        values = {"res_bank_obj": bank_id}
        return request.render("odoo_check_managment.res_bank_management_template", values)

    @http.route('/bank/check/update', type='http', auth="user", website=True)
    def res_bank_update_attrs(self, **post):
        is_updated = False
        if post.get("check_attribute_line_id"):
            is_updated = request.env["res.bank.attribute.line"].browse(
                int(post.get('check_attribute_line_id'))).write({
                    "top_displacement": int(post.get("y1", 0)),
                    "left_displacement": int(post.get("x1")) if post.get("x1") else 0,
                    "height": int(post.get("h")) if post.get("h") else 0,
                    "width": int(post.get("w")) if post.get("w") else 0,
                    # "font_size": post.get(""),
                    # "font_family": post.get(""),
                })
        values = {
            "res_bank_obj": request.env["res.bank"].browse(int(post.get('bank_id')))
            if post.get('bank_id') else False,
        }
        if is_updated:
            values.update({
                "updated_check_attribute_line_id": int(post.get('check_attribute_line_id'))
            })
        # return self.res_bank_management(
        #     bank_id=values.get("res_bank_obj"))
        # post = {}
        # return request.render("odoo_check_managment.res_bank_management_template", values)
        print('============= (values.get("res_bank_obj"))', (values.get("res_bank_obj")))
        return request.redirect("/bank/check/%s" % request.env['ir.http']._slug(values.get("res_bank_obj")))

    @http.route('/bank/check/preview/<model("res.bank"):bank_id>', type='http', auth="user", website=True)
    def res_bank_preview(self, bank_id, **post):
        values = {"res_bank_obj": bank_id}
        return request.render("odoo_check_managment.res_bank_priview", values)
