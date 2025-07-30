/** @odoo-module **/
import { makeContext } from "@web/core/context";
import { registry } from "@web/core/registry";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";

export class ChecksX2ManyField extends X2ManyField {
    async onAdd({ context, editable } = {}) {
        const domain =
            typeof this.props.domain === "function" ? this.props.domain() : this.props.domain;
        context = makeContext([this.props.context, context]);
        console.log("**************", this.list);
        console.log("**************", this.props);
        if (this.isMany2Many) {
            const { string } = this.props;
            const title = _t("Add: %s", string);
            return this.selectCreate({ domain, context, title });
        }

        if (editable) {
            if (this.list.editedRecord) {
                const proms = [];
                this.list.model.bus.trigger("NEED_LOCAL_CHANGES", { proms });
                await Promise.all([...proms, this.list.editedRecord._updatePromise]);
                await this.list.leaveEditMode({ canAbandon: false });
            }
            if (!this.list.editedRecord) {
                // Get the last record and update the due_date
                const lastRecord = this.list.records[0];
                console.log("**************", lastRecord);
                if (lastRecord) {
                    const lastDueDate = new Date(lastRecord.data.due_date);
                    console.log("lastRecord.data.due_date", lastRecord.data.due_date);
                    console.log("lastDueDate", lastDueDate);
                    lastDueDate.setMonth(lastDueDate.getMonth() + 1);
                    console.log("lastDueDate", lastDueDate);
                    console.log("lastDueDate", lastDueDate.setDate(lastDueDate.getDate()+1));
                    
                    // Check if the month increment caused the day to overflow and adjust
                    const lastCheckNo = parseInt(lastRecord.data.check_no) +1;

                    console.log("context",context)
                    context = { ...context,
                                default_due_date: lastDueDate,
                                default_check_no:lastCheckNo.toString(),
                                default_bank_id:lastRecord.data.bank_id[0],
                             };
                    console.log("new_context",context)
                }
                return this.addInLine({ context, editable });
            }
            return;
        }
        return this._openRecord({ context });
    }
}
export const checksX2ManyField = {
    ...x2ManyField,
    component: ChecksX2ManyField,
};

registry.category("fields").add("checks_x2many", checksX2ManyField);
