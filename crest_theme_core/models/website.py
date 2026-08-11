from odoo import fields, models, api , _
 
class Website(models.Model):
    _inherit = 'website'
    _description = 'Terabits Website Configurations' 
    
    phone_label = fields.Char(string="Phone Label",default='24 Support')
    phone_number= fields.Char(string='Phone Number')
    shipping_label= fields.Char(string='Shipping Label',default='Free Shipping')
    
    warning_message = fields.Char(string='Warning Message', default='Due to high demand, there may be a delay in processing and shipping your order. We appreciate your patience and understanding during this time.')
    warning_color = fields.Char(string='Warning Color', default='#664d03')
    warning_bg_color = fields.Char(string='Warning Background Color', default='#fff3cd')
    # pwa app configuration
    pwa_enable = fields.Boolean(string='Enable PWA')
    pwa_app_name = fields.Char('App Name')
    pwa_short_name = fields.Char('Short Name')
    pwa_description = fields.Char('App Description')
    pwa_image_192 = fields.Binary('Icon Image 192px')
    pwa_image_512 = fields.Binary('Icon Image 512px')
    pwa_start_url = fields.Char('App Start Url')
    pwa_background_color = fields.Char('Background Color')
    pwa_theme_color = fields.Char('Theme Color')
    pwa_shortcuts_ids = fields.Many2many('pwa.shortcuts',string='PWA Shortcuts')


    def get_theme_config(self):
        theme_config = self.env['theme.configuration'].sudo().search([('website_id','in',self.ids)],limit=1)
        return len(theme_config) and theme_config or False

    def get_b2b_config(self):
        user = self.env.user.sudo()
        b2b_config = False
        theme_config = self.env['theme.configuration'].sudo().search([('website_id','in',self.ids)],limit=1)
        if user.is_public and len(theme_config) and theme_config.b2b_mode:
            b2b_config = True
        return b2b_config

    def config_phone_shipping(self):
        user = self.env.user.sudo()
        data = {
            'phone_label': self.phone_label,
            'phone_number': self.phone_number,
            'shipping_label': self.shipping_label
        }
        return data 

    def get_category_menu(self): 
        theme_config = self.env['theme.configuration'].sudo().search([('website_id','in',self.ids)],limit=1) 
        if len(theme_config):
            return theme_config.h_category_menu
        return True
    
    def get_navbar_hover_effect(self): 
        theme_config = self.env['theme.configuration'].sudo().search([('website_id','in',self.ids)],limit=1) 
        if len(theme_config):
            return theme_config.navbar_hover_effect
        return 'navbar_hover_effect_1'
    
    def get_pricelist_offers(self): 
        pricelist_obj = self.env['product.pricelist'].search([('website_id','in',self.ids),('active', '=', True),('id','in',self.get_pricelist_available().ids)])
        if len(pricelist_obj) != 0:
            return pricelist_obj.item_ids.mapped('offer_name')
        else:
            return []      
    def get_h_offers_show(self):
        theme_config = self.env['theme.configuration'].sudo().search([('website_id','in',self.ids)],limit=1) 
        if len(theme_config) != 0:
            return theme_config.h_offer_line
        return True

    def get_h_cart_popup(self):
        theme_config = self.env['theme.configuration'].sudo().search([('website_id','in',self.ids)],limit=1) 
        if len(theme_config):
            return theme_config.h_cart_popup
        return True
    
    def warning_message_config(self):
        data = {
            'warning_message': self.warning_message,
            'warning_color': self.warning_color,
            'warning_bg_color': self.warning_bg_color
        }
        return data
    
class PwaShortcuts(models.Model):
    _name= "pwa.shortcuts"
    _description = "PWA Shortcuts"

    name = fields.Char(string='Name')
    website_id = fields.Many2one('website')  
    short_name = fields.Char('Short Name', translate=True)
    description = fields.Text('Description')
    url = fields.Char('Shortcut URL', required=True,)
    icon = fields.Binary('Icon')
