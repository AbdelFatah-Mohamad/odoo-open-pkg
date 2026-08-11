import { Editor } from "@html_editor/editor";
import { patch } from "@web/core/utils/patch";


patch(Editor.prototype, {
    attachTo(editable) {
        const snippetAreas = editable.querySelectorAll(".dynamic_snippet_template");
        snippetAreas.forEach(snippetArea => {
            snippetArea.innerHTML = `
                        <img src="/theme_crest/static/images/loader-bigsize-gif.gif"
                            alt="loading.."
                            class="col-12 col-md-6 mx-auto" />
                    `;
        });
        return super.attachTo(...arguments)
    }
})