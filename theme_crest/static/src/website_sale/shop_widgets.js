/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { _t } from "@web/core/l10n/translation";
const { DateTime } = luxon;
import { rpc } from "@web/core/network/rpc";
import { redirect } from '@web/core/utils/urls';
import { deserializeDateTime } from "@web/core/l10n/dates";
import wSaleUtils from "@website_sale/js/website_sale_utils";
import { Dialog } from '@web/core/dialog/dialog';
import { markup, onWillStart, onMounted, useRef } from "@odoo/owl";
import { WebsiteSale } from '@website_sale/interactions/website_sale';
import { patch } from '@web/core/utils/patch';

const cartHandlerMixin = wSaleUtils.cartHandlerMixin;

class ProductInquiryDialogBits extends Dialog {
    static components = { Dialog };
    static template = "theme_crest.ProductInquiryDialog";
    static props = {
        ...Dialog.props,
        product_id: { type: Number, optional: true },
        widget: { type: Object, optional: true },
        product_name: { type: String, optional: true },
    };
    static defaultProps = {
        ...Dialog.defaultProps,
        size: "xl",
        parent: Object,
    };
    setup() {
        super.setup();
        this.markup = markup;
        this.ProductInquiry = useRef("productinquirydialog");
        onWillStart(this.onWillStart);
        onMounted(this.onMounted);
    }
    async onWillStart() {
        var self = this
        let res = await rpc('/form/product-inquiry-form', { product_id: this.props.product_id, product_name: this.props.product_name });
        self.$content = res.productinquiry_dialog;
    }
    onMounted() {
        this.props.widget.trigger_up("widgets_start_request", {
            $target: $(this.ProductInquiry.el),
        });
    }
}
patch(WebsiteSale.prototype, {
    onChangeAttribute(ev) {
        const productGrid = this.el.querySelector('.o_wsale_products_grid_table_wrapper');
        if (productGrid) {
            productGrid.classList.add('opacity-50');
        }
        const form = wSaleUtils.getClosestProductForm(ev.currentTarget);
        const filters = form.querySelectorAll('input:checked, select');
        const attributeValues = new Map();
        const tags = new Set();
        const attribs = new Set()
        const in_stock = new Set()
        const rt = new Set()
        for (const filter of filters) {
            if (filter.value) {
                if (filter.name === 'attribute_value') {
                    // Group attribute value ids by attribute id.
                    const [attributeId, attributeValueId] = filter.value.split('-');
                    const valueIds = attributeValues.get(attributeId) ?? new Set();
                    valueIds.add(attributeValueId);
                    attributeValues.set(attributeId, valueIds);
                } else if (filter.name === 'tags') {
                    tags.add(filter.value);
                } else if (filter.name === 'attribs') {
                    attribs.add(filter.value)
                } else if (filter.name === 'in_stock') {
                    in_stock.add(filter.value)
                } else if (filter.name === 'rt') {
                    rt.add(filter.value)
                }
            }
        }
        const url = new URL(form.action);
        const searchParams = url.searchParams;
        // Aggregate all attribute values belonging to the same attribute into a single
        // `attribute_values` search param.
        for (const entry of attributeValues.entries()) {
            searchParams.append('attribute_values', `${entry[0]}-${[...entry[1]].join(',')}`);
        }
        // Aggregate all tags into a single `tags` search param.
        if (tags.size) {
            searchParams.set('tags', [...tags].join(','));
        }
        if (attribs.size) {
            searchParams.set('attribs', [...attribs].join(','));
        }
        if (in_stock.size) {
            searchParams.set('in_stock', [...in_stock].join(','));
        }
        if (rt.size) {
            searchParams.set('rt', [...rt].join(','));

        }
        redirect(`${url.pathname}?${searchParams.toString()}`);
    }

})

const ProductInquiryBits = publicWidget.Widget.extend({
    selector: '.s_website_form_crm',
    events: ({
        "click .s_website_form_send_submit": "SubmitForm",
    }),
    start(ele, otps) {
        this.notification = this.bindService("notification");
        return this._super.apply(this, arguments);
    },
    SubmitForm: async function (ev) {
        ev.preventDefault(ev);
        var self = this;
        var target = this.$el.find('form');
        const $button = target.find('.s_website_form_send_submit, .s_website_form_send_submit');
        $button.addClass('disabled').attr('disabled', 'disabled');
        target.find('#s_website_form_result, #o_website_form_result').empty();
        this.form_fields = target.serializeArray();
        var form_values = {};
        this.form_fields.forEach((input) => {
            if (input.name in form_values) {
                if (Array.isArray(form_values[input.name])) {
                    form_values[input.name].push(input.value);
                } else {
                    form_values[input.name] = [form_values[input.name], input.value];
                }
            } else {
                form_values[input.name] = input.value;
            }
        });

        // Include product_id in the form submission
        form_values.product_id = target.find('[name="product_id"]').val();

        rpc('/form/crm', form_values).then((result) => {
            if (result.success) {
                form_values = {};
                $button.removeClass('disabled').removeAttr('disabled');

                this.notification.add(result.message, {
                    title: _t("Form submit"),
                    type: "success",
                });
                window.location.reload()
            } else {
                this.notification.add(result.message, {
                    title: _t("Form submit"),
                    type: "danger",
                });
                $button.removeClass('disabled').removeAttr('disabled');

            }
        }).catch((error) => {
            console.error("Error submitting form:", error);
            $button.removeClass('disabled').removeAttr('disabled');

        });
    }
});

publicWidget.registry.ProductInquiryBits = ProductInquiryBits;

publicWidget.registry.WebsiteShopBits = publicWidget.Widget.extend({
    selector: ".oe_website_sale",
    events: {
        'scroll': '_stickyCart',
        'click .attr_container .attrib_value .fa-close': '_removeFilter',
    },
    start: function () {
        // $('img.lazyload').lazyload();
    },
    _stickyCart: function (ev) {
        var self = this;
        var target = self.$target.find('.sticky-product-container')
        if (self.$target.find('.js_sale.o_wsale_product_page').length != 0) {
            const top = self.$target.find('#add_to_cart').offset().top;
            const bottom = self.$target.find('#add_to_cart').offset().top + self.$target.find('#add_to_cart').outerHeight();
            const bottom_screen = $(window).scrollTop() + $(window).innerHeight();
            const top_screen = $(window).scrollTop();
            if ((bottom_screen > top) && (top_screen < bottom)) {
                target.toggleClass("show");
            } else {
                if (top < 0) {
                    target.toggleClass("show");
                }
            }
        }
    },
    _removeFilter(ev) {

        let aval = $(ev.currentTarget).parent().data().aval;
        let fname = $(ev.currentTarget).parent().data().fname;
        let href = window.location.href;
        let urlObj = new URL(href);
        let params = urlObj.searchParams;
        let newParams = new URLSearchParams();
        params.forEach((value, key) => {

            if (['attribs', 'tags', 'in_stock', 'rt'].includes(key)) {
                aval = aval.toString();
            }
            if (!(key === fname && value === aval)) {
                if (key === fname && fname === 'attribute_values') {
                    // value = "3-51,52,53"
                    let [attrId, ids] = value.split('-');
                    let values = ids.split(','); // ["51", "52", "53"]

                    let removeId = aval.split('-')[1]; // "51"

                    // filter out the removed id
                    values = values.filter(v => v !== removeId);

                    if (values.length > 0) {
                        newParams.append(key, attrId + '-' + values.join(','));
                    }
                } else {
                    newParams.append(key, value);
                }
            }


        });
        urlObj.search = newParams.toString();
        window.location.href = urlObj.href;
    }
});

publicWidget.registry.StickyProductDetail = publicWidget.Widget.extend({
    selector: "#wrapwrap",
    events: {
        'scroll': '_stickyCart',
    },
    _stickyCart: function (ev) {
        var self = this;
        if (self.$target.find('.sticky-product-container').length) {
            const top = self.$target.find('#add_to_cart').offset().top;
            const bottom = self.$target.find('#add_to_cart').offset().top + self.$target.find('#add_to_cart').outerHeight();
            const bottom_screen = $(window).scrollTop() + $(window).innerHeight();
            const top_screen = $(window).scrollTop();
            if ((bottom_screen > top) && (top_screen < bottom)) {
                if (self.$target.find('.sticky-product-container').hasClass("show")) {
                    self.$target.find('.sticky-product-container').removeClass("show");
                }
            } else {
                if (top < 0) {
                    if (!self.$target.find('.sticky-product-container').hasClass("show")) {
                        self.$target.find('.sticky-product-container').addClass("show");
                    }
                }
            }
        }
        var offset = 450;
        var $back_to_top = $('.sticky-product-container');
        ($('#wrapwrap').scrollTop() > offset) ? $back_to_top.addClass('show') : $back_to_top.removeClass('show');
    },
});


publicWidget.registry.ProductCardInquirybits = publicWidget.Widget.extend({
    selector: "#product_detail #product_details",
    events: {
        "click .btn-inquiry": "_onClickInquiry",
    },
    start: function () {
        this.orm = this.bindService("orm");
        return this._super.apply(this, arguments);
    },
    _onClickInquiry: function (ev) {
        var $targetData = $(ev.currentTarget).data();
        const ProductInquiry = new publicWidget.registry.ProductInquiryBits(this, {});
        this.call("dialog", "add", ProductInquiryDialogBits, {
            widget: ProductInquiry,
            product_id: $targetData.product_id,
            product_name: $targetData.product_name,

        });

    }
});

publicWidget.registry.PricelistOfferTimerbits = publicWidget.Widget.extend({
    selector: ".card_item_timer_bits",
    events: {},
    start: function () {
        var self = this;
        var offer_due = this.$el.find('.pl_offer_timer').data().offerends;
        this.offer_due = deserializeDateTime(offer_due);
        this.timer = setInterval(function () { self.start_countdown() }, 1000);
    },
    start_countdown() {
        var now = DateTime.now();
        const timeLeft = this.offer_due - now;
        if (timeLeft < 0) {
            clearInterval(this.timer);
            this.$el.hide();
            return;
        } else {
            this.$el.show();
        }
        // Calculate days, hours, minutes, and seconds
        const days = Math.floor(timeLeft / (1000 * 60 * 60 * 24));
        const hours = Math.floor((timeLeft % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
        const minutes = Math.floor((timeLeft % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((timeLeft % (1000 * 60)) / 1000);
        // Display the result
        if (!this.$el.find('.counter').hasClass('pading-space')) {
            this.$el.find('.counter').addClass('pading-space');
        }
        this.$el.find('.days').html(String(days));
        this.$el.find('.hours').html(String(hours));
        this.$el.find('.minutes').html(String(minutes));
        this.$el.find('.seconds').html(String(seconds));
    }
});




publicWidget.registry.BitsAddCartNotifier = publicWidget.Widget.extend({
    selector: ".bits-notify-container",
    events: {
        'click .recent-close': 'closeNotify',
    },
    init: function () {
        this._super.apply(this, arguments);
        this.rpc = rpc;
        this.init_notifier();
    },
    init_notifier: async function (ev) {
        let self = this
        setInterval(async function () {
            const response = await rpc('/get/cart_notify');
            if (response && response.product_id) {
                self.$el.empty();
                self.$el.append(response.popup_html);
                self.$el.toggleClass('make-toast');
                setTimeout(function () {
                    self.$el.toggleClass('make-toast');
                }, 5000);
            }
        }, 20000);
    },
    closeNotify: function (ev) {

        this.$el.empty();
    }
}) 
