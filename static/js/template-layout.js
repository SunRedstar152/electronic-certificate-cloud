(function templateLayoutModule(root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TemplateLayout = api;
    }
}(typeof window !== "undefined" ? window : globalThis, function createTemplateLayoutApi() {
    "use strict";

    const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, Number(value)));
    const clone = (value) => JSON.parse(JSON.stringify(value));

    function stableId(source) {
        const raw = String(source || "field").replace(/^custom\./, "").toLowerCase();
        const normalized = raw.replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
        return /^[a-z]/.test(normalized) ? normalized.slice(0, 64) : `field_${normalized || "value"}`.slice(0, 64);
    }

    function createElement(options) {
        const source = String(options.source || "custom.field");
        const kind = String(options.kind || "text");
        const result = {
            id: stableId(options.id || source),
            source,
            kind,
            label: String(options.label || source.replace(/^custom\./, "")),
            required: Boolean(options.required),
            visible: options.visible !== false,
            box: clone(options.box || { x: 0.25, y: 0.4, width: 0.5, height: 0.15 }),
        };
        if (kind !== "qr") {
            result.style = clone(options.style || {
                font: { source: "builtin", key: "sarabun" },
                font_size_px: 36,
                color: "#111111",
                weight: 400,
                align: "center",
                line_height: 1.25,
            });
        }
        if (["date", "time", "date_range", "time_range"].includes(kind)) {
            result.format = clone(options.format || {
                locale: "th-TH",
                calendar: "buddhist",
                pattern: "long",
                prefix: "",
                separator: " ถึง ",
                suffix: "",
            });
        }
        if (options.sample_value !== undefined) {
            result.sample_value = clone(options.sample_value);
        }
        if (kind === "qr" && options.show_id !== undefined) result.show_id = Boolean(options.show_id);
        if (kind === "signature") {
            result.source = "signature";
            result.id = stableId(options.id || "signature");
            result.required = true;
            result.style.align = "center";
            delete result.format;
            delete result.sample_value;
        }
        return result;
    }

    function moveElement(element, pointer, rect) {
        const next = clone(element);
        const normalizedX = (Number(pointer.clientX) - Number(rect.left)) / Number(rect.width);
        const normalizedY = (Number(pointer.clientY) - Number(rect.top)) / Number(rect.height);
        next.box.x = clamp(normalizedX, 0, 1 - next.box.width);
        next.box.y = clamp(normalizedY, 0, 1 - next.box.height);
        return next;
    }

    function addVerificationId(layout, qr, canvas = { width: 1600, height: 1131 }) {
        const original = clone(qr.box);
        const height = original.height * 0.18;
        qr.box.height = original.height - height;
        qr.show_id = false;
        const label = createElement({
            id: uniqueElementId(layout.elements, `${qr.id.slice(0, 58)}_id`),
            source: "verification_code", kind: "text", label: "ID เกียรติบัตร", required: false,
            visible: qr.visible !== false,
            box: { x: original.x, y: Math.min(1 - height, original.y + qr.box.height), width: original.width, height },
            style: { font: { source: "builtin", key: "sarabun" }, font_size_px: Math.max(8, Math.min(512, Math.round(original.width * canvas.width * 0.085))), color: "#111111", weight: 400, align: "center", line_height: 1 },
        });
        layout.elements.push(label);
        return label;
    }

    function ensureVerificationIds(layout, canvas) {
        if (layout.elements.some(element => element.source === "verification_code")) return layout;
        layout.elements.filter(element => element.kind === "qr" && element.show_id !== false).forEach(qr => addVerificationId(layout, qr, canvas));
        return layout;
    }

    function resizeElement(element, delta) {
        const next = clone(element);
        next.box.width = clamp(next.box.width + Number(delta.width || 0), 0.00001, 1 - next.box.x);
        next.box.height = clamp(next.box.height + Number(delta.height || 0), 0.00001, 1 - next.box.y);
        return next;
    }

    function nudgeElement(element, deltaX, deltaY) {
        const next = clone(element);
        next.box.x = clamp(next.box.x + Number(deltaX || 0), 0, 1 - next.box.width);
        next.box.y = clamp(next.box.y + Number(deltaY || 0), 0, 1 - next.box.height);
        return next;
    }

    function scaleBox(box, canvas) {
        return {
            left: Number(box.x) * Number(canvas.width),
            top: Number(box.y) * Number(canvas.height),
            width: Number(box.width) * Number(canvas.width),
            height: Number(box.height) * Number(canvas.height),
        };
    }

    function centerElement(element, direction = "both") {
        const next = clone(element);
        if (direction === "horizontal" || direction === "both") next.box.x = (1 - next.box.width) / 2;
        if (direction === "vertical" || direction === "both") next.box.y = (1 - next.box.height) / 2;
        return next;
    }

    function snapElementToCenters(element, elements, canvas, options = {}) {
        const next = clone(element);
        const guides = { x: null, y: null };
        if (options.disabled) return { element: next, guides };
        const peers = elements.filter((item) => item.id !== element.id && item.visible !== false);
        for (const [axis, size] of [["x", "width"], ["y", "height"]]) {
            const pixels = Number(canvas[size]);
            if (!(pixels > 0)) continue;
            const center = next.box[axis] + next.box[size] / 2;
            let nearestDistance = (options.thresholdPx === undefined ? 6 : options.thresholdPx) + 1e-9;
            const targets = [0.5, ...peers.map((item) => item.box[axis] + item.box[size] / 2)];
            for (const target of targets) {
                const position = target - next.box[size] / 2;
                const distance = Math.abs(center - target) * pixels;
                if (position < 0 || position > 1 - next.box[size] || distance >= nearestDistance) continue;
                nearestDistance = distance;
                next.box[axis] = position;
                guides[axis] = target;
            }
        }
        return { element: next, guides };
    }

    function canvasSize(dimensions, zoom, availableWidth) {
        const width = Number(dimensions.width);
        const height = Number(dimensions.height);
        if (zoom === "100%") {
            return { width, height };
        }
        const ratio = Math.min(1, Number(availableWidth) / width);
        return { width: width * ratio, height: height * ratio };
    }

    function fieldInputs(layout) {
        return (layout && Array.isArray(layout.elements) ? layout.elements : [])
            .filter((item) => item.visible !== false && String(item.source || "").startsWith("custom."))
            .map((item) => ({
                key: item.source.slice(7),
                id: item.id,
                label: item.label,
                kind: item.kind,
                required: Boolean(item.required),
                format: item.format ? clone(item.format) : null,
            }));
    }

    function mappingFields(layout) {
        return fieldInputs(layout).flatMap((field) => {
            if (["date_range", "time_range"].includes(field.kind)) {
                return [
                    { ...field, target: `custom.${field.key}.start`, label: `${field.label} (เริ่ม)` },
                    { ...field, target: `custom.${field.key}.end`, label: `${field.label} (สิ้นสุด)` },
                ];
            }
            return [{ ...field, target: `custom.${field.key}` }];
        });
    }

    function hasSignature(layout) {
        return Boolean(layout && Array.isArray(layout.elements)
            && layout.elements.some((item) => item.visible !== false
                && item.kind === "signature" && item.source === "signature"));
    }

    function uniqueElementId(elements, requestedBase) {
        const base = stableId(requestedBase);
        const existing = new Set((Array.isArray(elements) ? elements : []).map((item) => item.id));
        if (!existing.has(base)) return base;
        let counter = 2;
        while (existing.has(`${base}_${counter}`)) counter += 1;
        return `${base}_${counter}`;
    }

    function customFieldsFromValues(values, layout) {
        const result = {};
        fieldInputs(layout).forEach((field) => {
            if (["date_range", "time_range"].includes(field.kind)) {
                const start = String(values[`${field.key}.start`] || "").trim();
                const end = String(values[`${field.key}.end`] || "").trim();
                if (start || end) {
                    result[field.key] = { start, end };
                }
                return;
            }
            const value = String(values[field.key] || "").trim();
            if (value) {
                result[field.key] = value;
            }
        });
        return result;
    }

    function serialize(layout) {
        return JSON.stringify({
            version: 2,
            elements: clone(layout && Array.isArray(layout.elements) ? layout.elements : []),
        });
    }

    return Object.freeze({
        addVerificationId,
        canvasSize,
        centerElement,
        createElement,
        ensureVerificationIds,
        customFieldsFromValues,
        fieldInputs,
        hasSignature,
        mappingFields,
        moveElement,
        nudgeElement,
        resizeElement,
        scaleBox,
        snapElementToCenters,
        serialize,
        stableId,
        uniqueElementId,
    });
}));
