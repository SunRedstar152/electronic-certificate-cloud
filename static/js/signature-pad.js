(function signaturePadModule(root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.SignaturePad = api;
    }
}(typeof window !== "undefined" ? window : globalThis, function createSignaturePadApi() {
    "use strict";

    const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];
    const MAX_BYTES = 2 * 1024 * 1024;
    const MAX_WIDTH = 4096;
    const MAX_HEIGHT = 2048;
    let hostCounter = 0;

    function signatureError(code, message) {
        const error = new Error(message);
        error.code = code;
        return error;
    }

    function hasPngSignature(bytes) {
        return bytes.length >= PNG_SIGNATURE.length
            && PNG_SIGNATURE.every((value, index) => bytes[index] === value);
    }

    function analyzePixels(bytes) {
        let hasTransparency = false;
        let hasVisibleInk = false;
        for (let index = 3; index < bytes.length; index += 4) {
            const alpha = bytes[index];
            if (alpha < 255) hasTransparency = true;
            if (alpha > 0) hasVisibleInk = true;
        }
        return { hasTransparency, hasVisibleInk };
    }

    function canvasPoint(event, rect, canvas) {
        return {
            x: ((Number(event.clientX) - Number(rect.left)) / Number(rect.width)) * Number(canvas.width),
            y: ((Number(event.clientY) - Number(rect.top)) / Number(rect.height)) * Number(canvas.height),
        };
    }

    function buildSubmission(state, required) {
        if (!required) return null;
        const pngDataUrl = String(state.pngDataUrl || "");
        const signerName = String(state.signerName || "").trim();
        const signerPosition = String(state.signerPosition || "").trim();
        if (!pngDataUrl || !signerName || !signerPosition) {
            throw signatureError(
                "SIGNATURE_REQUIRED",
                "กรุณาใส่ลายเซ็น ชื่อ และตำแหน่งผู้ลงนาม",
            );
        }
        return {
            png_data_url: pngDataUrl,
            signer_name: signerName,
            signer_position: signerPosition,
        };
    }

    function mount(host) {
        if (!host || typeof host.querySelector !== "function") {
            throw new TypeError("A signature editor host is required.");
        }
        if (!host.id) {
            hostCounter += 1;
            host.id = `signature-editor-${hostCounter}`;
        }
        if (!host.querySelector('[data-signature-role="canvas"]')) {
            const template = document.getElementById("signature-editor-template");
            if (!template || !template.content) {
                throw new Error("The signature editor template is unavailable.");
            }
            host.appendChild(template.content.cloneNode(true));
        }

        const canvas = host.querySelector('[data-signature-role="canvas"]');
        const context = canvas.getContext("2d", { willReadFrequently: true });
        const upload = host.querySelector('[data-signature-role="upload"]');
        const name = host.querySelector('[data-signature-role="name"]');
        const position = host.querySelector('[data-signature-role="position"]');
        const preview = host.querySelector('[data-signature-role="preview-image"]');
        const error = host.querySelector('[data-signature-role="error"]');
        const clearButton = host.querySelector('[data-signature-role="clear"]');
        const modeButtons = Array.from(host.querySelectorAll("[data-signature-mode]"));
        const modePanels = Array.from(host.querySelectorAll("[data-signature-panel]"));
        const state = { pngDataUrl: "", signerName: "", signerPosition: "" };
        let required = false;
        let drawing = false;
        let lastPoint = null;
        let activeMode = null;

        [name, position, upload].forEach((control) => {
            if (!control) return;
            control.id = `${host.id}-${control.dataset.signatureRole}`;
            const field = control.closest(".field");
            const label = field && field.querySelector("label");
            if (label) label.htmlFor = control.id;
        });
        modeButtons.forEach((button) => {
            const mode = button.dataset.signatureMode;
            const panel = host.querySelector(`[data-signature-panel="${mode}"]`);
            if (!panel) return;
            button.id = `${host.id}-${mode}-tab`;
            panel.id = `${host.id}-${mode}-panel`;
            button.setAttribute("aria-controls", panel.id);
            panel.setAttribute("aria-labelledby", button.id);
        });

        function showError(message) {
            if (!error) return;
            error.textContent = message || "";
            error.hidden = !message;
        }

        function publishCanvas() {
            const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
            const analysis = analyzePixels(pixels);
            if (!analysis.hasVisibleInk) {
                throw signatureError("SIGNATURE_EMPTY", "กรุณาวาดหรือเลือกภาพลายเซ็นที่ไม่ว่าง");
            }
            if (!analysis.hasTransparency) {
                throw signatureError("SIGNATURE_TRANSPARENCY_REQUIRED", "ไฟล์ลายเซ็นต้องมีพื้นหลังโปร่งใส");
            }
            state.pngDataUrl = canvas.toDataURL("image/png");
            preview.src = state.pngDataUrl;
            preview.hidden = false;
            showError("");
        }

        function pointerDown(event) {
            event.preventDefault();
            drawing = true;
            lastPoint = canvasPoint(event, canvas.getBoundingClientRect(), canvas);
            if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
        }

        function pointerMove(event) {
            if (!drawing || !lastPoint) return;
            const point = canvasPoint(event, canvas.getBoundingClientRect(), canvas);
            if (point.x === lastPoint.x && point.y === lastPoint.y) return;
            context.beginPath();
            context.moveTo(lastPoint.x, lastPoint.y);
            context.lineTo(point.x, point.y);
            context.strokeStyle = "#111111";
            context.lineWidth = 6;
            context.lineCap = "round";
            context.lineJoin = "round";
            context.stroke();
            lastPoint = point;
        }

        function pointerUp(event) {
            if (!drawing) return;
            drawing = false;
            if (canvas.hasPointerCapture && canvas.hasPointerCapture(event.pointerId)) {
                canvas.releasePointerCapture(event.pointerId);
            }
            try {
                publishCanvas();
            } catch (caught) {
                showError(caught.message);
            }
        }

        async function uploadChanged() {
            const file = upload.files && upload.files[0];
            if (!file) return;
            if (Number(file.size) > MAX_BYTES) {
                clearSignature();
                showError("ไฟล์ลายเซ็นมีขนาดใหญ่เกิน 2 MiB");
                return;
            }
            let bitmap = null;
            try {
                const header = new Uint8Array(await file.slice(0, 8).arrayBuffer());
                if (!hasPngSignature(header)) {
                    throw signatureError("SIGNATURE_INVALID_PNG", "กรุณาเลือกไฟล์ PNG พื้นหลังโปร่งใส");
                }
                bitmap = await createImageBitmap(file);
                if (bitmap.width > MAX_WIDTH || bitmap.height > MAX_HEIGHT) {
                    throw signatureError("SIGNATURE_TOO_LARGE", "ภาพลายเซ็นมีมิติเกิน 4096 × 2048 พิกเซล");
                }
                const scratch = document.createElement("canvas");
                scratch.width = bitmap.width;
                scratch.height = bitmap.height;
                const scratchContext = scratch.getContext("2d", { willReadFrequently: true });
                scratchContext.drawImage(bitmap, 0, 0);
                const sourceAnalysis = analyzePixels(
                    scratchContext.getImageData(0, 0, scratch.width, scratch.height).data,
                );
                if (!sourceAnalysis.hasTransparency) {
                    throw signatureError("SIGNATURE_TRANSPARENCY_REQUIRED", "ไฟล์ลายเซ็นต้องมีพื้นหลังโปร่งใส");
                }
                if (!sourceAnalysis.hasVisibleInk) {
                    throw signatureError("SIGNATURE_EMPTY", "ภาพลายเซ็นไม่มีส่วนที่มองเห็นได้");
                }
                context.clearRect(0, 0, canvas.width, canvas.height);
                const ratio = Math.min(canvas.width / bitmap.width, canvas.height / bitmap.height);
                const width = bitmap.width * ratio;
                const height = bitmap.height * ratio;
                context.drawImage(bitmap, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
                publishCanvas();
            } catch (caught) {
                clearSignature();
                showError(caught.message || "ไม่สามารถอ่านไฟล์ลายเซ็นได้");
            } finally {
                if (bitmap && bitmap.close) bitmap.close();
            }
        }

        function clearSignature() {
            drawing = false;
            lastPoint = null;
            context.clearRect(0, 0, canvas.width, canvas.height);
            upload.value = "";
            preview.removeAttribute("src");
            preview.hidden = true;
            state.pngDataUrl = "";
            showError("");
        }

        function reset() {
            clearSignature();
            name.value = "";
            position.value = "";
            state.signerName = "";
            state.signerPosition = "";
        }

        function setMode(mode, shouldFocus = false) {
            if (activeMode !== null && activeMode !== mode) clearSignature();
            activeMode = mode;
            modeButtons.forEach((button) => {
                const selected = button.dataset.signatureMode === mode;
                button.setAttribute("aria-selected", String(selected));
                button.tabIndex = selected ? 0 : -1;
                if (selected && shouldFocus) button.focus();
            });
            modePanels.forEach((panel) => {
                panel.hidden = panel.dataset.signaturePanel !== mode;
            });
        }

        canvas.addEventListener("pointerdown", pointerDown);
        canvas.addEventListener("pointermove", pointerMove);
        canvas.addEventListener("pointerup", pointerUp);
        canvas.addEventListener("pointercancel", pointerUp);
        upload.addEventListener("change", () => uploadChanged());
        clearButton.addEventListener("click", clearSignature);
        modeButtons.forEach((button) => {
            button.addEventListener("click", () => setMode(button.dataset.signatureMode));
            button.addEventListener("keydown", (event) => {
                const current = modeButtons.indexOf(button);
                let next = null;
                if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (current + 1) % modeButtons.length;
                if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + modeButtons.length) % modeButtons.length;
                if (event.key === "Home") next = 0;
                if (event.key === "End") next = modeButtons.length - 1;
                if (next === null) return;
                event.preventDefault();
                setMode(modeButtons[next].dataset.signatureMode, true);
            });
        });
        if (modeButtons.length) setMode(modeButtons[0].dataset.signatureMode);

        return Object.freeze({
            setRequired(value) {
                const nextRequired = Boolean(value);
                if (required && !nextRequired) reset();
                required = nextRequired;
                host.hidden = !required;
                name.required = required;
                position.required = required;
            },
            submission() {
                state.signerName = name.value;
                state.signerPosition = position.value;
                try {
                    const value = buildSubmission(state, required);
                    showError("");
                    return value;
                } catch (caught) {
                    showError(caught.message);
                    throw caught;
                }
            },
            reset,
            focus() {
                name.focus();
            },
        });
    }

    function mountGroup(host) {
        let editors = [];
        let layoutKey = null;
        let focusedEditor = null;
        if (!host.id) host.id = `signature-group-${++hostCounter}`;
        host.classList.add("signature-editor-group");

        function reset() {
            editors.forEach(({ editor }) => editor.reset());
            focusedEditor = null;
        }

        return Object.freeze({
            setLayout(layout, templateId) {
                const elements = (layout && Array.isArray(layout.elements) ? layout.elements : [])
                    .filter((item) => item.visible !== false && item.kind === "signature");
                const nextKey = JSON.stringify([templateId, elements]);
                if (nextKey === layoutKey) return;
                reset();
                host.replaceChildren();
                layoutKey = nextKey;
                editors = elements.map((element, index) => {
                    const child = document.createElement("div");
                    child.id = `${host.id}-${element.id}`;
                    child.dataset.signatureElementId = element.id;
                    host.appendChild(child);
                    const editor = mount(child);
                    child.querySelector("legend").textContent = `${index + 1}. ${element.label || "ลายเซ็นผู้ลงนาม"}`;
                    editor.setRequired(true);
                    return { id: element.id, editor };
                });
                host.hidden = editors.length === 0;
            },
            submission() {
                if (!editors.length) return null;
                return Object.fromEntries(editors.map(({ id, editor }) => {
                    try {
                        return [id, editor.submission()];
                    } catch (error) {
                        focusedEditor = editor;
                        editor.focus();
                        throw error;
                    }
                }));
            },
            reset,
            focus() {
                const editor = focusedEditor || (editors[0] && editors[0].editor);
                if (editor) editor.focus();
            },
        });
    }

    return Object.freeze({
        analyzePixels,
        buildSubmission,
        canvasPoint,
        hasPngSignature,
        mount,
        mountGroup,
    });
}));
