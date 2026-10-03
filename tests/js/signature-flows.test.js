const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const layout = require("../../static/js/template-layout.js");

function dashboard() {
    const controls = new Map();
    const document = {
        getElementById: () => ({}),
        querySelector(selector) {
            if (!controls.has(selector)) controls.set(selector, { value: "", checked: false });
            return controls.get(selector);
        },
    };
    const source = fs.readFileSync(path.join(__dirname, "../../static/js/dashboard.js"), "utf8")
        .replace("    bootstrap();", `
            renderTemplateEditor = () => {};
            renderTemplateProperties = () => {};
            scheduleTemplatePreview = () => {};
            toast = () => {};
            window.testing = { state, addTemplateElement, singleIssuancePayload, singleIssuanceAttempt, updateIssuanceSignatureEditors };
        `);
    const window = { TemplateLayout: layout };
    vm.runInNewContext(source, { window, document, location: new URL("https://example.test/dashboard"), crypto: require("node:crypto").webcrypto });
    return { ...window.testing, controls, document };
}

test("adding three signature blocks keeps independent IDs and editable labels", () => {
    const app = dashboard();
    app.document.querySelector("#template-new-kind").value = "signature";
    for (const label of ["ประธาน", "คณบดี", "พยาน"]) {
        app.document.querySelector("#template-new-label").value = label;
        app.addTemplateElement();
    }
    assert.deepEqual(Array.from(app.state.templateLayout.elements, item => item.id), ["signature", "signature_2", "signature_3"]);
    assert.deepEqual(Array.from(app.state.templateLayout.elements, item => item.label), ["ประธาน", "คณบดี", "พยาน"]);
    app.state.templateLayout.elements.splice(1, 1);
    app.addTemplateElement();
    assert.equal(new Set(app.state.templateLayout.elements.map(item => item.id)).size, 3);
});

test("template selection passes each flow its complete layout and template identity", () => {
    const app = dashboard();
    const first = { id: 1, layout: { version: 2, elements: [
        layout.createElement({ kind: "signature", id: "signature" }),
        layout.createElement({ kind: "signature", id: "signature_2" }),
    ] } };
    const second = { id: 2, layout: { version: 2, elements: [] } };
    app.state.templates = [first, second];
    app.document.querySelector("#single-template").value = "1";
    app.document.querySelector("#bulk-template").value = "2";
    const received = [];
    for (const flow of ["single", "bulk"]) {
        app.state.signatureEditors[flow] = { setLayout(value, key) { received.push([flow, value, key]); } };
    }
    app.updateIssuanceSignatureEditors();
    assert.deepEqual(received, [["single", first.layout, 1], ["bulk", second.layout, 2]]);
});

test("single issuance sends each signature and changes its idempotency key when any signer changes", () => {
    const app = dashboard();
    app.state.templates = [{ id: 1, layout: { version: 2, elements: [
        layout.createElement({ kind: "signature", id: "signature" }),
        layout.createElement({ kind: "signature", id: "signature_2" }),
    ] } }];
    app.document.querySelector("#single-template").value = "1";
    app.document.querySelector("#single-custom-fields").querySelectorAll = () => [];
    app.document.querySelector("#single-name").value = "Recipient";
    app.document.querySelector("#single-topic").value = "Activity";
    app.document.querySelector("#single-date").value = "2026-09-06";
    const signatures = {
        signature: { png_data_url: "data:image/png;base64,AAAA", signer_name: "First", signer_position: "Dean" },
        signature_2: { png_data_url: "data:image/png;base64,BBBB", signer_name: "Second", signer_position: "Chair" },
    };
    app.state.signatureEditors.single = { submission: () => structuredClone(signatures) };
    const first = app.singleIssuanceAttempt();
    assert.deepEqual(first.payload.signatures, signatures);
    assert.equal(first.payload.signature, undefined);
    assert.equal(app.singleIssuanceAttempt().attempt.key, first.attempt.key);
    signatures.signature_2.signer_name = "Replacement";
    assert.notEqual(app.singleIssuanceAttempt().attempt.key, first.attempt.key);
});
