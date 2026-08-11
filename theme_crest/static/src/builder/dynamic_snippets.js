import { Plugin } from "@html_editor/plugin";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { BuilderAction } from "@html_builder/core/builder_action";
import { Dialog } from "@web/core/dialog/dialog";
import { Component, useState } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { renderToElement } from "@web/core/utils/render";
import { carouselConfigs } from "../js/static_template/config_carousel";
import { ThemeDialog, SnippetEditorDialog } from "../snippet_editor/snippetEditorDialog/snippetEditorDialog";
import { withSequence } from "@html_editor/utils/resource";
import { WEBSITE_BACKGROUND_OPTIONS } from "@website/builder/option_sequence";
import { after } from "@html_builder/utils/option_sequence";
import { onMounted } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";


export class DynamicSnippetManageAction extends BuilderAction {
    static id = "dynamic_snippet_manage";

    async apply({ editingElement }) {
        const dialogService = this.services.dialog;   // ✅ fetch dialog service

        const snippetDiv = editingElement.querySelector(".dynamic_snippet");

        let data = [];

        try {
            const parsed = snippetDiv?.dataset.data ? JSON.parse(snippetDiv.dataset.data) : [];
            data = Array.isArray(parsed) ? parsed : [];
        } catch (e) {
            console.warn("Invalid dataset.data JSON", e);
            data = [];
        }

        // optional backend fetch
        if (data.model && data.template_key) {
            const result = await rpc("/website/snippet/get", {
                model: data.model,
                template_key: data.template_key,
                visual: data.visual || {},
                data: data,
            });
            data = Array.isArray(result) ? result : [];
        }

        // ✅ open dialog properly
        dialogService.add(SnippetEditorDialog, {
            title: _t("Manage Dynamic Snippet"),
            bodyProps: {
                data,
                save: async (newData) => {
                    snippetDiv.dataset.data = JSON.stringify(newData);
                    console.log(newData);

                    let containerAry = [];
                    let sliderConfi = [];

                    console.log(newData.length);

                    if (newData) {
                        await Promise.all(newData.map(async (row) => {
                            let container = $("<div class='container'></div>");
                            let row_template = $("<div class='row'></div>");

                            await Promise.all(row.map(async (col) => {
                                let snippet_class = (col?.data?.model === "static_template")
                                    ? "snp_static_bits"
                                    : "snp_dynamic_bits";

                                let col_template = $(
                                    `<div class='${this._getResponsiveClasses(col?.class)}  ${snippet_class}'></div>`
                                );

                                if (col?.data?.model === "static_template") {
                                    let col_inside;
                                    try {
                                        col_inside = renderToElement(col?.visual?.template || "theme_crest.no_records");
                                    } catch {
                                        col_inside = renderToElement("theme_crest.no_records");
                                    }
                                    col_template.append(col_inside);
                                } else {
                                    try {
                                        let col_template = $(`<div class='${snippet_class} ${this._getResponsiveClasses(col?.class)} '>
                                            <div class="dynamic_snippet_template"><img src="/theme_crest/static/images/loader-bigsize-gif.gif" alt="loading.." class="col-12 col-md-6 mx-auto" />
                                            </div>`);
                                        col_template.attr("data-snp_config", JSON.stringify(col));
                                        row_template.append(col_template);
                                        // const res_view = await rpc("/website/snippet/get", {

                                        //     model: col.data.model,
                                        //     template_key: col.visual.template || "dy_prod_tmp_style_1_bits",
                                        //     visual: col.visual,
                                        //     data: col.data,
                                        // });
                                        // console.log(`this not ${res_view}`);

                                        // let col_inside = res_view.view;

                                        // if (col.visual.type === "slider") {
                                        //     $(col_inside).find(".owl-carousel")
                                        //         .addClass(`dy-prod-owl-carousel-style-${col._uid}`);

                                        //     sliderConfi.push({
                                        //         selector: `dy-prod-owl-carousel-style-${col._uid}`,
                                        //         options: {
                                        //             loop: true,
                                        //             margin: 0,
                                        //             dots: col.visual.dost,
                                        //             nav: col.visual.nav,
                                        //             autoplay: col.visual.autoplay,
                                        //             items: Number(col.visual.limit),
                                        //             responsive: {
                                        //                 0: { items: 1 },
                                        //                 600: { items: Math.min(Number(col.visual.limit), 2) },
                                        //                 1000: { items: Math.min(Number(col.visual.limit), 3) },
                                        //                 1200: { items: Math.min(Number(col.visual.limit), 5) },
                                        //                 1400: { items: Number(col.visual.limit) },
                                        //             },
                                        //         },
                                        //     });
                                        // }

                                        // col_template.append(col_inside);
                                    } catch {
                                        col_template.append(renderToElement("theme_crest.no_records"));
                                    }
                                }

                                col_template.attr("data-snp_config", JSON.stringify(col));
                                row_template.append(col_template);
                            }));

                            if (row[0]?.container) {
                                container.removeClass("container").addClass("container-fluid");
                            }
                            container.append(row_template);
                            containerAry.push(container);
                        }));
                    } else {
                        // fallback: empty state
                        // let container = $("<div class='container'></div>");
                        // let row_template = $("<div class='row'></div>");
                        // let col_template = $("<div class='col-12 '></div>");
                        // col_template.append(renderToElement("theme_crest.not_data_available"));
                        // row_template.append(col_template);
                        // container.append(row_template);
                        // containerAry.push(container);
                        let container = $("<div class='container'></div>");
                        let row_template = $("<div class='row'></div>");
                        let col_template = $(`<div class='col-12 '></div>`);
                        let col_inside = renderToElement('theme_crest.not_data_available', {});
                        let col = {
                            "class": "col-12",
                            "_uid": `${this.getRandomUid()}`,
                            "container": false,
                            "data": {
                                "model": "static_template"
                            },
                            "visual": {
                                "template": "theme_crest.not_data_available",
                                "limit": 4,
                                "type": "grid",
                                "buttons": [],
                                "dost": false,
                                "nav": true,
                                "autoplay": false,
                                "borderstyle": "rounded"
                            }
                        }
                        col_template.append(col_inside);
                        col_template.attr("data-snp_config", JSON.stringify(col));
                        container.append(row_template);
                        containerAry.push(container)
                    }

                    // ✅ replace only inside current snippetDiv
                    if (containerAry.length != 0) {
                        $(snippetDiv).empty().append(containerAry);
                    }
                    this._initializeCarousels(snippetDiv);

                    console.log("✅ Snippet data saved:", newData);


                },
            },
        });
    }

    getRandomUid() {
        var S4 = function () {
            return (((1 + Math.random()) * 0x10000) | 0).toString(16).substring(1);
        };
        return S4();
    }
    _getResponsiveClasses(colClass) {
        const colSize = Number(colClass?.replace("col-", ""));
        const sizeMap = {
            12: "col-12",
            11: "col-12 col-lg-11",
            10: "col-12 col-lg-10",
            9: "col-12 col-lg-9",
            8: "col-12 col-lg-8",
            7: "col-12 col-lg-7",
            6: "col-12 col-lg-6",
            4: "col-12 col-md-6 col-lg-4",
            3: "col-12 col-md-6 col-lg-3 col-xl-3",
            2: "col-12 col-lg-3 col-md-4 col-sm-6 col-xl-2",
            1: "col-12 col-lg-3 col-md-4 col-sm-6 col-xl-1",
        };
        return sizeMap[colSize] || "col-12";
    }
    _initializeCarousels(snippetDiv) {
        // Initialize all carousels after rendering
        carouselConfigs.forEach(config => {
            const $carousel = $(snippetDiv).find(config.selector);
            if ($carousel.length) {
                $carousel.owlCarousel(config.options);
            }
        });
    }

}


class DynamicSnippetsBitsPlugin extends Plugin {
    static id = "DynamicSnippetsBits";
    static dependencies = ["builderActions"];  // 👈 mandatory

    setup() {

        // preload action so we can call it in handlers
        this.manageAction = this.dependencies.builderActions.getAction(
            "dynamic_snippet_manage"
        );
    }
    resources = {
        builder_options: [
            {
                template: "theme_crest.DynamicSnippetsBits",
                selector: ".theme-bits-builder-snippet",
                editableOnly: false,
                title: _t("Dynamic Snippets Bits"),
            },
        ],
        builder_actions: {
            dynamic_snippet_manage: DynamicSnippetManageAction,
        },
        on_snippet_dropped_handlers: [
            async ({ snippetEl }) => {
                if (snippetEl.classList.contains("theme-bits-builder-snippet")) {
                    // call the builder action

                    await this.manageAction.apply({ editingElement: snippetEl });
                }
            },
        ],
    };
}

registry.category("website-plugins").add(DynamicSnippetsBitsPlugin.id, DynamicSnippetsBitsPlugin);



export class HotspotEnabledAction extends BuilderAction {
    static id = "o_hotspot_enabled";

    getValue({ editingElement }) {
        // return current state

        if (editingElement.dataset.hotspotEnabled === "true") {
            return "on";
        }
        if (editingElement.dataset.hotspotEnabled === "false") {
            return "off";
        }
        // fallback: wrapper detection
        if (editingElement.querySelector(".o_hotspot_wrapper")) {
            return "on";
        }
        return editingElement.dataset.hotspotEnabled;
    }

    isApplied({ editingElement, value }) {

        // check if current state matches UI selection
        return this.getValue({ editingElement }) === value;
    }

    apply({ editingElement, value }) {

        const figure = editingElement.parentElement
        if (!figure) return;
        figure.style.position = "relative"
        // Check if hotspot section already exists
        let hotspotBlock = figure.querySelector('.hotspot_draggable');

        if (value === 'on') {
            if (!hotspotBlock) {
                hotspotBlock = document.createElement('section');
                hotspotBlock.className = 'hotspot_draggable target_hotspot_tag o_not_editable   s_col_no_resize s_col_no_bgcolor fade_hotspot shape-none show_icon o_colored_level';
                hotspotBlock.innerHTML = `
                <a class="show_hotspot hotspot-link ">
                <i class="fa fa-plus fa-fw hotspot-icon o_editable" contenteditable="false"></i>
                </a>
                `;
                hotspotBlock.setAttribute("style", "--hotspot-border-color: #red;")
                editingElement.parentElement.appendChild(hotspotBlock);
                // this._makeDraggable(hotspotBlock);
            }

            const img = figure.querySelector('img');
            if (img) img.dataset.hotspotEnabled = 'true';
        } else {
            // Remove hotspot section
            const hotspots = figure.querySelectorAll('.hotspot_draggable');
            hotspots.forEach(h => h.remove());

            const img = figure.querySelector('img');
            if (img) img.dataset.hotspotEnabled = 'false';
        }
    }
    // _makeDraggable(el) {
    //     let isDragging = false, offsetX, offsetY;

    //     el.style.position = "absolute";

    //     el.addEventListener("pointerdown", (ev) => {
    //         isDragging = true;
    //         el.setPointerCapture(ev.pointerId);

    //         const rect = el.getBoundingClientRect();
    //         const parentRect = el.parentElement.getBoundingClientRect();

    //         offsetX = ev.clientX - rect.left + parentRect.left;
    //         offsetY = ev.clientY - rect.top + parentRect.top;

    //         ev.preventDefault();
    //     });

    //     el.addEventListener("pointermove", (ev) => {
    //         if (!isDragging) return;

    //         const parentRect = el.parentElement.getBoundingClientRect();

    //         // new position = mouse - offset inside parent
    //         let newLeft = ev.clientX - offsetX;
    //         let newTop = ev.clientY - offsetY;

    //         // clamp inside parent
    //         newLeft = Math.max(0, Math.min(newLeft, parentRect.width - el.offsetWidth));
    //         newTop = Math.max(0, Math.min(newTop, parentRect.height - el.offsetHeight));

    //         el.style.left = newLeft + "px";
    //         el.style.top = newTop + "px";
    //     });

    //     el.addEventListener("pointerup", (ev) => {
    //         isDragging = false;
    //         el.releasePointerCapture(ev.pointerId);
    //     });

    //     // prevent accidental click
    //     el.addEventListener("click", (ev) => {
    //         if (isDragging) ev.stopImmediatePropagation();
    //     });

    // }

}


class ImageHotspotPlugin extends Plugin {
    static id = "imageHotspotPlugin";
    resources = {
        builder_actions: {
            o_hotspot_enabled: HotspotEnabledAction,
        },
    };
}

// Register the plugin in website-plugins
registry.category("website-plugins").add(ImageHotspotPlugin.id, ImageHotspotPlugin);

class SetHotspotUrl extends BuilderAction {
    static id = "setHotspotUrl";

    getValue({ editingElement }) {
        const link = editingElement.querySelector("a.hotspot-link");
        return link?.getAttribute("href") || "";
    }

    apply({ editingElement, value }) {
        let icon = editingElement.querySelector(".hotspot-icon");
        let link = editingElement.querySelector("a.hotspot-link");

        // If no link yet, wrap the icon
        if (!link && icon) {
            link = document.createElement("a");
            link.classList.add("hotspot-link");

            // move the icon inside <a>
            editingElement.insertBefore(link, icon);
            link.appendChild(icon);
        }

        if (link) {
            link.setAttribute("href", value);
            link.dataset.hotspotUrl = value;
        }
    }

    clean({ editingElement }) {
        const link = editingElement.querySelector("a.hotspot-link");
        if (link) {
            // unwrap icon when cleaning
            const icon = link.querySelector(".hotspot-icon");
            if (icon) {
                link.parentNode.insertBefore(icon, link);
            }
            link.remove();
        }
    }
}
class SetHotspotProduct extends BuilderAction {
    static id = "setHotspotProduct";

    apply({ editingElement, value }) {
        // `value` is JSON string → parse

        let product;
        try {
            product = typeof value === "string" ? JSON.parse(value) : value;
        } catch (e) {
            console.warn("Invalid JSON for product:", value);
            return;
        }
        if (!product || !product.id) return;

        const productUrl = `/shop/product/${product.id}`;
        let link = editingElement.querySelector("a.hotspot-link");

        if (!link) {
            link = document.createElement("a");
            link.classList.add("hotspot-link");
            editingElement.appendChild(link);

            // move icon if exists
            const icon = editingElement.querySelector(".hotspot-icon");
            if (icon) {
                link.appendChild(icon);
            }
        }

        // set URL + dataset
        link.href = productUrl;
        link.dataset.product = JSON.stringify(product);  // ✅ important
        editingElement.dataset.product = JSON.stringify(product);  // ✅ important


    }

    getValue({ editingElement }) {
        const link = editingElement.querySelector("a.hotspot-link");
        // ✅ Always return JSON string
        return link ? link.dataset.product || null : null;
    }

    clean({ editingElement }) {
        const link = editingElement.querySelector("a.hotspot-link");
        if (link) {
            const icon = link.querySelector(".hotspot-icon");
            if (icon) {
                editingElement.appendChild(icon);
            }
            link.remove();
        }
    }

}

class HotspotClassPlugin extends Plugin {
    static id = "hotspotClassPlugin";
    selector = ".hotspot_draggable";

    resources = {
        builder_options: [
            withSequence(after(WEBSITE_BACKGROUND_OPTIONS), {
                template: "theme_crest.hotspotClassPlugin",
                selector: ".hotspot_draggable",
            }),
            withSequence(after(WEBSITE_BACKGROUND_OPTIONS), {
                template: "theme_crest.ColorHotspotOption",
                selector: ".hotspot_draggable",
                applyTo: ".hotspot-icon",
            }),

        ],
        builder_actions: {
            SetHotspotUrl, SetHotspotProduct,
        },

    };

}
registry.category("website-plugins").add(HotspotClassPlugin.id, HotspotClassPlugin);
