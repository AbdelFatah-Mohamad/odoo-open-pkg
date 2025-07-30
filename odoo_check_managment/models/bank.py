import logging
import base64
import io

from odoo import models, fields, api, _
from odoo.osv import expression
from odoo.exceptions import UserError

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

_logger = logging.getLogger(__name__)

class BankCheck(models.Model):
    _inherit = 'res.bank'
    _description = 'Bank Check'

    # Fields declaration
    main_check_image = fields.Image("Check Image")
    check_image = fields.Image("Processed Check Image", compute="_compute_check_image", store=True)

    @api.depends('main_check_image', 'check_height', 'check_width', 'check_measure_unit')
    def _compute_check_image(self):
        if not Image or not ImageOps:
            _logger.warning("PIL library not found, check image resizing will not work.")
            for record in self:
                record.check_image = record.main_check_image
            return

        try:
            DPI = self.env.ref('odoo_check_managment.bank_check_paperformat', raise_if_not_found=False).dpi or 90
        except Exception:
            DPI = 90

        UNIT_TO_INCHES = {'cm': 1 / 2.54, 'in': 1.0, 'mm': 1 / 25.4}

        for record in self:
            if not (record.main_check_image and record.check_width > 0 and record.check_height > 0 and record.check_measure_unit):
                record.check_image = record.main_check_image
                continue

            width_px, height_px = 0, 0
            if record.check_measure_unit == 'px':
                width_px, height_px = int(record.check_width), int(record.check_height)
            else:
                width_in = record.check_width * UNIT_TO_INCHES.get(record.check_measure_unit, 0)
                height_in = record.check_height * UNIT_TO_INCHES.get(record.check_measure_unit, 0)
                width_px, height_px = int(width_in * DPI), int(height_in * DPI)

            if width_px > 0 and height_px > 0:
                try:
                    image_stream = io.BytesIO(base64.b64decode(record.main_check_image))
                    img = Image.open(image_stream)
                    # Correct image orientation based on EXIF data
                    img = ImageOps.exif_transpose(img)
                    resized_img = img.resize((width_px, height_px))
                    buffer = io.BytesIO()
                    resized_img.save(buffer, format='PNG')
                    record.check_image = base64.b64encode(buffer.getvalue())
                except Exception as e:
                    _logger.error(f"Failed to resize image for bank check {record.id}: {e}")
                    record.check_image = record.main_check_image
            else:
                record.check_image = record.main_check_image

    check_attribute_line_ids = fields.One2many("res.bank.attribute.line",
                                                "bank_id",
                                                "Check Attributes")
    check_height = fields.Float(string='Check Height',
                                #  default=lambda self: self.env.ref('odoo_check_managment.bank_check_paperformat').print_page_height if self.env.ref('odoo_check_managment.bank_check_paperformat') else 93,
                                 required=True)
    check_width = fields.Float(string='Check Width',
                                # default=lambda self: self.env.ref('odoo_check_managment.bank_check_paperformat').print_page_width if self.env.ref('odoo_check_managment.bank_check_paperformat') else 203,
                                required=True)
    max_char_in_line1 = fields.Integer(
        "Maximum Characters",
        help="Maximum characters in 'Amount in words Line1' field.\n Aplicable if Check attributes has both attributes(Amount In words Line1 & Amount In words Line2)"
    )
    check_measure_unit = fields.Selection([("cm", "CM"),
                                            ("in", "Inches"),
                                            ("mm", "MM"),
                                            ("px", "Pixels")],
                                           "Measurement Unit",
                                           default="mm",
                                           required=True)

    @api.depends('bic')
    def _compute_display_name(self):
        for bank in self:
            name = (bank.name or '') + (bank.bic and (' - ' + bank.bic) or '')
            bank.display_name = name

    @api.model
    def _name_search(self, name, domain=None, operator='ilike', limit=None, order=None):
        domain = domain or []
        if name:
            name_domain = ['|', ('bic', '=ilike', name + '%'), ('name', operator, name)]
            if operator in expression.NEGATIVE_TERM_OPERATORS:
                name_domain = ['&', '!'] + name_domain[1:]
            domain = domain + name_domain
        return self._search(domain, limit=limit, order=order)

    @api.onchange('country')
    def _onchange_country_id(self):
        if self.country and self.country != self.state.country_id:
            self.state = False

    @api.onchange('state')
    def _onchange_state(self):
        if self.state.country_id:
            self.country = self.state.country_id

    def redirect_to_res_bank_page(self):
        self.ensure_one()
        if not self.check_attribute_line_ids:
            raise UserError(
                _('First you have to set bank check attributes. '
                  'Then you will be able to configure attribute(s) values'))
        return {
            'type': 'ir.actions.act_url',
            'target': '_blank',
            'url': "/bank/check/%s" % self.id,
        }

class BankCheckAttributeLine(models.Model):
    # Private attributes
    _name = 'res.bank.attribute.line'
    _description = 'Check Attribute Line'

    name = fields.Many2one("res.bank.attribute",
                           string='Name',
                           required=True)
    bank_id = fields.Many2one("res.bank",
                                     string='Bank Check',
                                     required=True)
    font_size = fields.Integer(string='Font Size', default=20)
    font_family = fields.Char(string='Font Family')
    letter_spacing = fields.Integer(string='Letter Spacing', default=0)
    top_displacement = fields.Integer(string='Top displacement')
    left_displacement = fields.Integer(string='Left displacement')
    bottom_displacement = fields.Integer(string='Bottom displacement')
    right_displacement = fields.Integer(string='Right displacement')
    height = fields.Integer(string='Height')
    width = fields.Integer(string='Width')

    def reset_values(self):
        for obj in self:
            obj.write({
                "top_displacement": 0,
                "left_displacement": 0,
                "bottom_displacement": 0,
                "right_displacement": 0,
                "height": 0,
                "width": 0,
            })

    @api.onchange("name")
    def onchange_name(self):
        if self.name and self.name.attribute == "check_date" and not self.letter_spacing:
            self.letter_spacing = 12


class BankCheckAttribute(models.Model):
    # Private attributes
    _name = 'res.bank.attribute'
    _description = 'Bank Check Attribute'

    name = fields.Char(string='Name', required=True)
    attribute = fields.Selection(
        [('check_date', "Date"), ('pay_line1', "Pay Line 1"),
         ('pay_line2', "Pay Line 2"),
         ('amount_line_1', "Amount Line 1 (in words)"),
         ('amount_line_2', "Amount Line 2 (in words)"),
         ('amount_box', "Amount Box"), ('account_number', "Account Number"),
         ('ac_pay', "A/C Pay Label")],
        required=True)
    demo_data = fields.Char("Demo Data For Preview")
    demo_data_date = fields.Date("Demo Date For Preview",
                                 default=fields.Date.today())
    date_format = fields.Selection([("ddMMyyyy", "DD MM YYYY"),
                                    ("MMddyyyy", "MM DD YYYY"),
                                    ("dd/MM/yyyy","DD/MM/YYYY"),
                                    ("MM/dd/yyyy","MM/DD/YYYY")],
                                   "Date Format",
                                   default="ddMMyyyy")

class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _prepare_html(self, html, report_model=False):
        bodies, res_ids, header, footer, specific_paperformat_args = super(
            IrActionsReport, self)._prepare_html(html, report_model=False)
        for rec in self:
            if rec.model == "res.bank" and bodies:
                bodies = [
                    bytes(bodies[0].replace(
                        b'class="container"', b'class="" style="margin:0px"').replace(
                            b'class="article o_report_layout_clean"', b'class=""'))
                ]
        return bodies, res_ids, header, footer, specific_paperformat_args
