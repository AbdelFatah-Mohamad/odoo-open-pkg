import { SnippetModel } from "@html_builder/snippets/snippet_service";
import { patch } from "@web/core/utils/patch";

patch(SnippetModel.prototype, {
    constructor(services, params) {
        // Call original constructor
        const res = SnippetModel.prototype.constructor.call(this, services, params);
        // Add your custom props
        this.snippetsByCategory.snippet_bits = [];

        return res;
    },
    get snippetBits() {
        return this.snippetsByCategory.snippet_bits
    }

});