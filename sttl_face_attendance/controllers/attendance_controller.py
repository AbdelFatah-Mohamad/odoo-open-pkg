# -*- coding: utf-8 -*-

from odoo import http, _
from odoo.http import request


class HrAttendance(http.Controller):
    @http.route('/employee/images', type="json", auth="public")
    def get_employee_images(self, employee_id=None):
        if employee_id:
            employees = request.env['hr.employee'].sudo().search([('id', '=', employee_id)])
            for employee in employees:
                employee_data = [{"employee_id": employee.id, "image": employee.image_1920}]
                return employee_data
        else:
            employees = request.env['hr.employee'].sudo().search([])
            employee_data = []
            for employee in employees:
                employee_data.append({"employee_id": employee.id, "image":employee.image_1920})
            return employee_data
