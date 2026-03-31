/* @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { Component, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ensureJQuery } from '@web/core/ensure_jquery';

class CaptureEmployeeImage extends Component {
    static template = "CaptureEmployeeImage";

    setup() {
        super.setup();
        this.employee_id = this.props.action.params.employee_id || this.props.action.params.active_id;
        this.orm = useService("orm");
        this.action = useService("action");
        try {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                alert("Unable to access the camera");
                this.env.config.historyBack();
            } else {
                onMounted(async () => {
                    await ensureJQuery();
                    this._start_video_stream();
                    this._bind_events();
                })
            }
        }
        catch (error) {
            console.log(error);
        }
    }

    _start_video_stream() {
        const self = this;
        setTimeout(() => {
            navigator.mediaDevices.getUserMedia({ video: true })
                .then(function (stream) {
                    var video = document.getElementById('video');
                    video.srcObject = stream;
                })
                .catch(function (err) {
                    self._stop_stream();
                    alert("Unable to access the camera");
                    self.env.config.historyBack();
                });
        }, 500);
    }

    _bind_events() {
        $('#btn-close').on('click', this._on_close.bind(this));
        $('#btn-click').on('click', this._on_capture.bind(this));
        $('#btn-close-sub').on('click', this._on_close.bind(this));
    }

    _on_capture() {
        try {
            const self = this;

            var video = document.getElementById('video');
            var canvas = document.getElementById('canvas');
            var context = canvas.getContext('2d');

            const targetWidth = 320;
            const targetHeight = 240;

            canvas.width = targetWidth;
            canvas.height = targetHeight;

            context.drawImage(video, 0, 0, targetWidth, targetHeight);

            var imageData = canvas.toDataURL('image/png');

            this.orm.call('hr.employee', 'register_face',[this.employee_id, imageData])
            .then(function (result) {
                self._on_close();
            });
        } catch {
            this._on_close();
        }
    }

    _stop_stream() {
        var video = document.getElementById('video');
        if (video && video.srcObject) {
            let tracks = video.srcObject.getTracks();
            tracks.forEach(track => track.stop());
            video.srcObject = null;
        }
    }

    _on_close() {
        this._stop_stream();
        this.env.config.historyBack();
    }
    
}

registry.category("actions").add("new_employee_image", CaptureEmployeeImage);
