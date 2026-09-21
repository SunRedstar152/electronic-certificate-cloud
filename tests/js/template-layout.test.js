const test = require("node:test");
const assert = require("node:assert/strict");

const layout = require("../../static/js/template-layout.js");

const element = {
    id: "department",
    source: "custom.department",
    kind: "text",
    label: "หน่วยงาน",
    required: false,
    visible: true,
    box: { x: 0.1, y: 0.2, width: 0.4, height: 0.1 },
    style: {
        font: { source: "builtin", key: "sarabun" },
        font_size_px: 36,
        color: "#111111",
        weight: 400,
        align: "center",
        line_height: 1.25,
    },
};

test("moving at fit zoom stores normalized original coordinates", () => {
    const next = layout.moveElement(
        element,
        { clientX: 400, clientY: 250 },
        { left: 100, top: 50, width: 600, height: 400 },
    );
    assert.equal(next.box.x, 0.5);
    assert.equal(next.box.y, 0.5);
    assert.equal(element.box.x, 0.1);
});

test("moving and resizing clamp the element inside the canvas", () => {
    const moved = layout.moveElement(
        element,
        { clientX: 1000, clientY: 1000 },
        { left: 0, top: 0, width: 600, height: 400 },
    );
    assert.equal(moved.box.x, 0.6);
    assert.equal(moved.box.y, 0.9);
    const resized = layout.resizeElement(element, { width: 2, height: -1 });
    assert.equal(resized.box.width, 0.9);
    assert.ok(resized.box.height > 0);
    assert.ok(resized.box.height < element.box.height);
    assert.equal(resized.box.y, element.box.y);
    assert.ok(resized.box.y + resized.box.height <= 1);
});

test("100 percent zoom uses original pixel dimensions", () => {
    assert.deepEqual(
        layout.canvasSize({ width: 1600, height: 1131 }, "100%", 900),
        { width: 1600, height: 1131 },
    );
    assert.deepEqual(
        layout.canvasSize({ width: 1600, height: 1131 }, "fit", 800),
        { width: 800, height: 565.5 },
    );
});

test("field inputs follow visible custom layout elements", () => {
    const fields = layout.fieldInputs({
        version: 2,
        elements: [
            element,
            { ...element, id: "hidden", source: "custom.hidden", visible: false },
            { ...element, id: "name", source: "recipient_name" },
            {
                ...element,
                id: "period",
                source: "custom.period",
                kind: "date_range",
                required: true,
            },
        ],
    });
    assert.deepEqual(fields.map((field) => field.key), ["department", "period"]);
    assert.equal(fields[1].kind, "date_range");
    assert.equal(fields[1].required, true);
});

test("custom fields serialize scalar and range values", () => {
    const certificateLayout = {
        version: 2,
        elements: [
            element,
            {
                ...element,
                id: "period",
                source: "custom.period",
                kind: "date_range",
            },
        ],
    };
    assert.deepEqual(
        layout.customFieldsFromValues(
            {
                department: "  Computing  ",
                "period.start": "2026-08-13",
                "period.end": "2026-08-15",
            },
            certificateLayout,
        ),
        {
            department: "Computing",
            period: { start: "2026-08-13", end: "2026-08-15" },
        },
    );
});

test("new elements receive stable normalized defaults", () => {
    const created = layout.createElement({
        source: "custom.note",
        kind: "multiline",
        label: "หมายเหตุ",
    });
    assert.equal(created.id, "note");
    assert.equal(created.style.font.key, "sarabun");
    assert.deepEqual(created.box, { x: 0.25, y: 0.4, width: 0.5, height: 0.15 });
});

test("mapping fields expand range endpoints in template order", () => {
    const fields = layout.mappingFields({
        version: 2,
        elements: [
            element,
            { ...element, id: "period", source: "custom.period", kind: "date_range", label: "ช่วงอบรม", required: true },
        ],
    });
    assert.deepEqual(fields.map((field) => field.target), [
        "custom.department",
        "custom.period.start",
        "custom.period.end",
    ]);
    assert.equal(fields[1].required, true);
    assert.match(fields[1].label, /เริ่ม/);
});

test("unique element ids keep repeated QR additions valid", () => {
    const elements = [
        { id: "verification_qr" },
        { id: "verification_qr_2" },
    ];
    assert.equal(layout.uniqueElementId(elements, "verification_qr"), "verification_qr_3");
});

test("signature elements use required centered defaults and stay out of custom fields", () => {
    const signature = layout.createElement({
        source: "signature",
        id: "signature",
        kind: "signature",
        label: "ลายเซ็นผู้ลงนาม",
    });
    const certificateLayout = { version: 2, elements: [signature] };
    assert.equal(signature.required, true);
    assert.equal(signature.style.align, "center");
    assert.equal(layout.hasSignature(certificateLayout), true);
    assert.deepEqual(layout.fieldInputs(certificateLayout), []);
    assert.deepEqual(layout.mappingFields(certificateLayout), []);
    assert.deepEqual(layout.customFieldsFromValues({}, certificateLayout), {});
});

test("centering positions the whole block without changing its size or text alignment", () => {
    const horizontal = layout.centerElement(element, "horizontal");
    assert.deepEqual(horizontal.box, { x: 0.3, y: 0.2, width: 0.4, height: 0.1 });
    const vertical = layout.centerElement(element, "vertical");
    assert.deepEqual(vertical.box, { x: 0.1, y: 0.45, width: 0.4, height: 0.1 });
    const both = layout.centerElement(element, "both");
    assert.deepEqual(both.box, { x: 0.3, y: 0.45, width: 0.4, height: 0.1 });
    assert.deepEqual(both.style, element.style);
    assert.deepEqual(element.box, { x: 0.1, y: 0.2, width: 0.4, height: 0.1 });
    assert.deepEqual(JSON.parse(layout.serialize({ elements: [both] })).elements[0].box, both.box);
});

test("center snapping uses six screen pixels at fit and original zoom", () => {
    const nearby = { ...element, box: { ...element.box, x: 0.305, y: 0.447 } };
    const fit = layout.snapElementToCenters(nearby, [], { width: 600, height: 400 });
    assert.deepEqual(fit.element.box, { x: 0.3, y: 0.45, width: 0.4, height: 0.1 });
    assert.deepEqual(fit.guides, { x: 0.5, y: 0.5 });
    const original = layout.snapElementToCenters(nearby, [], { width: 1600, height: 1131 });
    assert.equal(original.element.box.x, 0.305);
    assert.equal(original.element.box.y, 0.45);
    assert.equal(original.guides.x, null);
    assert.equal(original.guides.y, 0.5);
});

test("snapping aligns to other visible block centers and ignores the selected block", () => {
    const moving = { ...element, box: { x: 0.15, y: 0.555, width: 0.2, height: 0.1 } };
    const peer = { ...element, id: "other", box: { x: 0.6, y: 0.5, width: 0.3, height: 0.2 } };
    const hidden = { ...peer, id: "hidden", visible: false, box: { x: 0.05, y: 0.05, width: 0.4, height: 0.1 } };
    const result = layout.snapElementToCenters(moving, [moving, peer, hidden], { width: 600, height: 400 });
    assert.equal(result.element.box.x, 0.15);
    assert.ok(Math.abs(result.element.box.y - 0.55) < 1e-10);
    assert.deepEqual(result.guides, { x: null, y: 0.6 });
});

test("free movement and unreachable centers never snap or move a block off the page", () => {
    const near = { ...element, box: { ...element.box, x: 0.301 } };
    const free = layout.snapElementToCenters(near, [], { width: 600, height: 400 }, { disabled: true });
    assert.deepEqual(free.element.box, near.box);
    assert.deepEqual(free.guides, { x: null, y: null });
    const nearEdge = { ...element, box: { x: 0, y: 0, width: 0.8, height: 0.8 } };
    const peer = { ...element, id: "edge", box: { x: 0.345, y: 0.345, width: 0.1, height: 0.1 } };
    const result = layout.snapElementToCenters(nearEdge, [peer], { width: 600, height: 400 });
    assert.deepEqual(result.element.box, nearEdge.box);
    assert.deepEqual(result.guides, { x: null, y: null });
});
