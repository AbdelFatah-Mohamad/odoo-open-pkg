/** @odoo-module **/

import { Dialog } from '@web/core/dialog/dialog';
import publicWidget from "@web/legacy/js/public/public_widget";
import { _t } from '@web/core/l10n/translation';
import { rpc } from "@web/core/network/rpc";
import { markup, onWillStart, onMounted, useRef } from "@odoo/owl";


export class MiniCartProductBits extends Dialog {
    static components = { Dialog };
    static template = "theme_crest.MiniCartDialog";
    static props = {
        ...Dialog.props,
        widget: { type: Object, optional: true },
        close: { type: Function, optional: true },
    };
    static defaultProps = {
        ...Dialog.defaultProps,
        size: "xl",
    };
    setup() {
        super.setup();
        this.markup = markup;
        this.dialogTitle = this.props.title;
        this.MiniCartDialog = useRef("minicartdialog");
        onWillStart(this.onWillStart);
        onMounted(this.onMounted);
    }
    async onWillStart() {
        let res = await rpc('/get_mini_cart', {});
        this.$content = res?.mini_cart_bits;
    }
    onMounted() {

        if (this.props.widget) {
            this.props.widget.trigger_up("widgets_start_request", {
                $target: $(this.MiniCartDialog.el),
            });
        }
    }
}

publicWidget.registry.MiniCartBits = publicWidget.Widget.extend({
    selector: "header",
    events: {
        'click .min-cart-bits': '_show_mini_cart',
    },
    _show_mini_cart: function (ev) {
        ev.preventDefault();
        this.call("dialog", "add", MiniCartProductBits, { widget: this });

    },
});