from odoo import http
from odoo.http import request
import json

class PwaWebApp(http.Controller): 

    @http.route('/pwa/service_worker', type='http', auth="public",website=True,sitemap=False)
    def service_worker(self):
        qweb = request.env['ir.qweb'].sudo()
        website = request.env['website'].sudo().get_current_website()
        mimetype = 'text/javascript;charset=utf-8'
        content = qweb._render('theme_crest.service_worker_bits', {
            'website_id': website.id,
        })
        return request.make_response(content, [('Content-Type', mimetype)])

    @http.route('/get/pwa/manifest', type='http', auth="public", website=True)
    def get_pwa_manifest(self, website_id=None):
        website = request.env['website'].sudo().get_current_website() 
        vals = {
            "name": website.pwa_app_name or "My E-commerce PWA",
            "short_name": website.pwa_short_name or "PWA",
            "start_url": website.pwa_start_url or "/shop",
            "display": "standalone",
            "scope": "/",
            "background_color": website.pwa_background_color or "#ffffff",
            "theme_color": website.pwa_theme_color or "#000000",
            "icons": [
                {
                    "src": f'/web/image/website/{website.id}/pwa_image_192/512x512',
                    "sizes": "192x192",
                    "type": "image/png"
                },
                {
                    "src": f'/web/image/website/{website.id}/pwa_image_512/512x512',
                    "sizes": "512x512",
                    "type": "image/png"
                }
            ],
            "shortcuts": [
                {
                    "name": sid.name,
                    "short_name": sid.short_name,
                    "description": sid.description,
                    "url": sid.url,
                    "icon": [
                        {
                            "src": f"/web/image/pwa.shortcuts/{sid.id}/icon",
                            "sizes": "192x192",
                            "type": "image/png"
                        }
                    ]
                } for sid in website.pwa_shortcuts_ids
            ]
        }

        # Return JSON manifest
        return request.make_response(
            json.dumps(vals),
            [('Content-Type', 'application/json;charset=utf-8')]
        )
