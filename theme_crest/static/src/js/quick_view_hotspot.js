/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { QuickViewdialogBits } from "../website_sale/quick_view";
var registry = publicWidget.registry;

registry.QuickView = publicWidget.Widget.extend({
    selector: '#wrapwrap',
    events: {
        'click .quickviewbits': '_QuickViewBits',
    },
    start: function () {
        this._observeHotspotWidth();

        $(window).on('resize', this._observeHotspotWidth.bind(this));
        return this._super.apply(this, arguments);
    },

    _observeHotspotWidth: function () {

        $('.target_hotspot_tag').each(function () {
            var $hotspot = $(this);
            var leftPosition = parseFloat($hotspot.css('left')) + 30 || 0;
            var windowWidth = $hotspot.parent().outerWidth();

            if (leftPosition >= windowWidth) {
                $hotspot.addClass('d-none');
            } else {
                $hotspot.removeClass('d-none');
            }
        });
    },
    _QuickViewBits: function (ev) {
        ev.preventDefault();

        try {
            let data = JSON.parse(ev.currentTarget.dataset.product);
            let product_id = data.id;

            if (product_id) {
                this.call("dialog", "add", QuickViewdialogBits, {
                    widget: this,
                    product_id: product_id,
                });
            }
        } catch (error) {
            console.error("Invalid JSON in dataset.product:", ev.currentTarget.dataset.product, error);
        }
    },


});
