/** @odoo-module **/
import { patch } from '@web/core/utils/patch';
import { WebsiteSale } from '@website_sale/interactions/website_sale';
import { patchDynamicContent } from '@web/public/utils';

patch(WebsiteSale.prototype, {
    setup() {
        super.setup();
        patchDynamicContent(this.dynamicContent, {
            '.o_wsale_product_btn_bits .a-submit': {
                't-on-click': this.onClickAdd.bind(this),
            },

        });
    },
    _onChangeCombination(ev, parent, combination) {
        super._onChangeCombination(...arguments);
        const skuEl = this.el.querySelector('.p_sku_bits');
        if (skuEl) {
            skuEl.textContent = arguments[2].product_sku || '';
        }
    }
});
