/** @odoo-module **/
import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import comparisonUtils from '@website_sale_comparison/js/website_sale_comparison_utils';
import wSaleUtils from '@website_sale/js/website_sale_utils';

export class ProductButtonsBits extends Interaction {
    static selector = '.snp_dynamic_bits'; // main page wrapper

    setup() {
        // Initial binding
        this._bindButtons();

        // Observe DOM changes to bind buttons in dynamic snippets
        const observer = new MutationObserver(() => this._bindButtons());
        observer.observe(this.el, { childList: true, subtree: true });
    }

    _bindButtons() {

        this.el.querySelectorAll('.o_add_compare:not([data-bound])').forEach(el => {
            el.dataset.bound = 'true';
            el.addEventListener('click', this.addProductComparison.bind(this));
        });

        // this.el.querySelectorAll('.o_add_wishlist:not([data-bound])').forEach(el => {
        //     el.dataset.bound = 'true';
        //     el.addEventListener('click', this.addProductWishlist.bind(this));
        // });

        this.el.querySelectorAll('a.btn_cart_bits:not([data-bound])').forEach(el => {
            el.dataset.bound = 'true';
            el.addEventListener('click', this.addToCart.bind(this));
        });
    }

    addProductComparison(ev) {

        const el = ev.currentTarget;
        const productId = parseInt(el.dataset.productProductId);
        if (!productId) return;

        if (comparisonUtils.getComparisonProductIds().includes(productId)) {
            el.classList.add('disabled');
            el.disabled = true;
            return;
        }

        comparisonUtils.addComparisonProduct(productId);
        el.classList.add('disabled');
        el.disabled = true;
    }

    // async addProductWishlist(ev) {
    //     const el = ev.currentTarget;
    //     const productId = parseInt(el.dataset.productProductId);
    //     const templateId = parseInt(el.dataset.productTemplateId);
    //     if (!productId) return;

    //     await rpc('/website_sale/wishlist/update', {
    //         product_id: productId,
    //         product_template_id: templateId,
    //     });

    //     el.classList.add('disabled');
    //     el.disabled = true;
    // }

    async addToCart(ev) {
        const el = ev.currentTarget;
        const productId = parseInt(el.dataset.productProductId);
        const productTemplateId = parseInt(el.dataset.productTemplateId);
        if (!productId) return;

        try {
            await this.env.services.cart.add(
                { productId, productTemplateId },
                { showQuantity: false }
            );
        } catch (error) {
            console.error("Cart add error:", error);
        }
    }
}

registry.category('public.interactions')
    .add('theme_crest.product_buttons_bits', ProductButtonsBits);


export class MiniCartProductBits extends Interaction {
    static selector = ".mini-cart-style-bits";
    setup() {
        this.dynamicContent = {
            "a.css_quantity_plus, a.css_quantity_minus": {
                "t-on-click.prevent.withTarget": this.onUpdateQty.bind(this),
            },
            "input.js_quantity[data-product-id]": {
                "t-on-change.prevent.withTarget": this.debounced(this.onChangeQty.bind(this), 500),
            },
            ".js_delete_product": {
                "t-on-click.prevent.withTarget": this.onClickRemoveProduct.bind(this),
            },
            "a.js_add_suggested_products": {
                "t-on-click.prevent.withTarget": this.onClickSuggestedProduct.bind(this),
            },
        };
    }

    async onUpdateQty(ev, el) {
        const input = el.closest('.css_quantity').querySelector('input.js_quantity');
        let quantity = parseInt(input.value || 0);

        if (el.classList.contains('css_quantity_plus')) {
            quantity += 1;
        } else if (el.classList.contains('css_quantity_minus')) {
            quantity = Math.max(quantity - 1, 0);
        }

        const lineId = parseInt(input.dataset.lineId);
        const productId = parseInt(input.dataset.productId);

        await rpc("/shop/cart/update", {
            line_id: lineId,
            quantity: quantity,
            product_id: productId
        });

        await this.refreshMiniCart();
    }

    async onChangeQty(ev, el) {
        const quantity = parseInt(el.value || 0);
        const lineId = parseInt(el.dataset.lineId);
        const productId = parseInt(el.dataset.productId);

        await rpc("/shop/cart/update", {
            line_id: lineId,
            quantity: quantity,
            product_id: productId
        });

        await this.refreshMiniCart();
    }

    async onClickRemoveProduct(ev, el) {
        const lineId = parseInt(el.dataset.lineId);
        const productId = parseInt(el.dataset.productId);

        await rpc("/shop/cart/update", {
            line_id: lineId,
            quantity: 0,
            product_id: productId
        });

        await this.refreshMiniCart();
    }

    async onClickSuggestedProduct(ev, el) {
        const lineId = 0; // No line_id yet
        const productId = parseInt(el.dataset.productId);

        await rpc("/shop/cart/update", {
            line_id: lineId,
            quantity: 1,
            product_id: productId
        });

        await this.refreshMiniCart();
    }

    async refreshMiniCart() {
        const res = await rpc("/get_mini_cart", {});
        if (res?.mini_cart_bits) {
            this.el.innerHTML = res.mini_cart_bits;

            // Update navbar cart quantity
            const qty = res.cart_quantity || 0;
            wSaleUtils.updateCartNavBar({ cart_quantity: qty, warning: '' });
        }
    }

}

registry.category("public.interactions").add("theme_crest.mini_cart_bits", MiniCartProductBits);


