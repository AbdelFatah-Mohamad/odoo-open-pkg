/* @odoo-module */


import { patch } from "@web/core/utils/patch";
import attendanceApp from "@hr_attendance/public_kiosk/public_kiosk_app";
import { rpc } from "@web/core/network/rpc";
const MODEL_URL = '/sttl_face_attendance/static/face-api/weights/';


patch(attendanceApp.kioskAttendanceApp.prototype, {
    setup() {
        super.setup();
    },
    
    initiateFaceAttendance: async function (event) {
        await this.setupCamera();
    },

    async onManualSelection(employeeId, enteredPin) {
        await this.setupCamera(employeeId);
    },

    async setupCamera(employeeId) {
        await Promise.all([
            faceapi.nets.tinyFaceDetector.load(MODEL_URL),
            faceapi.nets.faceLandmark68Net.load(MODEL_URL),
            faceapi.nets.faceRecognitionNet.load(MODEL_URL)
        ]);

        return new Promise(async (resolve) => {
            const overlay = this._createOverlay();
            var video;
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ video: true });
                const video = this._setupVideoStream(stream, overlay);
                this._bindAutoCapture(video, overlay, resolve, employeeId);
                await this._addEventListeners(video, overlay, resolve);
            } catch (error) {
                alert("Unable to access the camera");
                this._handleError(video, overlay, resolve);
            }
        });
    },

    async _bindAutoCapture(video, overlay, resolve, employeeId) {
        const self = this;
        let attempts = 0;
        const employeeDetails = await rpc('/employee/images',{
            employee_id: employeeId,
        });
        
        this.autoCaptureIntervalID = setInterval(async () => {
            try {
                if (++attempts >= 5) {
                    alert('No matching employee found.');
                    clearInterval(self.autoCaptureIntervalID);
                    self.autoCaptureIntervalID = null;
                    self._handleError(video, overlay, resolve);
                }
                const faceDetection = await faceapi.detectSingleFace(video, new faceapi.TinyFaceDetectorOptions())
                    .withFaceLandmarks()
                    .withFaceDescriptor();

                if (!faceDetection) {
                    return;
                }

                const matchingEmployeeId = await self._findMatchingEmployee(faceDetection, employeeDetails);

                if (matchingEmployeeId) {
                    clearInterval(self.autoCaptureIntervalID);
                    self.autoCaptureIntervalID = null;
                    await self._handleEmployeeDetected(matchingEmployeeId, video, overlay, resolve);
                }
            } catch (error) {
                alert('Face detection failed.');
                clearInterval(self.autoCaptureIntervalID);
                self.autoCaptureIntervalID = null;
                self._handleError(video, overlay, resolve);
            }
        }, 5000);
    },

    _createOverlay() {
        const overlay = document.createElement('div');
        overlay.id = 'camera_overlay';
        overlay.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.8);
            z-index: 1000;
            display: flex;
            align-items: center;
            justify-content: center;
        `;
        document.body.appendChild(overlay);
        return overlay;
    },

    _setupVideoStream(stream, overlay) {
        const camDiv = document.createElement('div');
        camDiv.id = 'cam-div';
        overlay.appendChild(camDiv);

        const video = document.createElement('video');
        video.id = 'camera-stream';
        camDiv.appendChild(video);

        const closeButton = document.createElement('button');
        closeButton.id = 'close-button';
        closeButton.textContent = 'Close Camera';
        closeButton.style.marginTop = '10px';
        camDiv.appendChild(closeButton);

        video.srcObject = stream;
        video.play();

        return video;
    },

    _addEventListeners(video, overlay, resolve) {
        const self = this;

        document.getElementById('close-button').addEventListener('click', () => {
            self._handleError(video, overlay, resolve);
        });
    },

    async _findMatchingEmployee(faceDetection, employeeDetails) {
        for (const { employee_id, image } of employeeDetails) {
            if (!image) continue;

            const blob = this._base64ToBlob(image, 'image/png');
            const referenceImage = await faceapi.bufferToImage(blob);

            var referenceDescriptor;
            try {
                referenceDescriptor = await faceapi.detectSingleFace(referenceImage, new faceapi.TinyFaceDetectorOptions())
                    .withFaceLandmarks()
                    .withFaceDescriptor();
            } catch {
                continue;
            }
            if (referenceDescriptor) {
                const distance = faceapi.euclideanDistance(faceDetection.descriptor, referenceDescriptor.descriptor);
                if (distance < 0.45) return employee_id;
            }
        }
        return null;
    },

    async _handleEmployeeDetected(employeeId, video, overlay, resolve) {
        this.employee_id = employeeId;
        this._stopStream(video);
        overlay.remove();

        const result = await this.makeRpcWithGeolocation('manual_selection',
            {
                'token': this.props.token,
                'employee_id': employeeId,
                'pin_code': false
            })
        if (result && result.attendance) {
            this.employeeData = result
            this.switchDisplay('greet')
        }else{
            if (enteredPin){
                this.displayNotification(_t("Wrong Pin"))
            }
        }

    },

    _handleError(video, overlay, resolve) {
        if (this.autoCaptureIntervalID) {
            window.clearInterval(this.autoCaptureIntervalID);
            this.autoCaptureIntervalID = null;
        }
        if (video){
            this._stopStream(video);
        }
        overlay.remove();
        resolve(false);
    },

    _stopStream(video) {
        if (video.srcObject) {
            video.srcObject.getTracks().forEach(track => track.stop());
            video.srcObject = null;
        }
    },

    _base64ToBlob(base64, mimeType) {
        const byteCharacters = atob(base64.split(',')[1] || base64);
        const byteArrays = [];

        for (let offset = 0; offset < byteCharacters.length; offset += 512) {
            const slice = byteCharacters.slice(offset, offset + 512);
            const byteArray = new Uint8Array([...slice].map(char => char.charCodeAt(0)));
            byteArrays.push(byteArray);
        }

        return new Blob(byteArrays, { type: mimeType });
    }
});