// Run with Playwright installed: node tests/js/signature-editors.browser.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
    const root = path.resolve(__dirname, "../..");
    const browser = await chromium.launch({ channel: "msedge", headless: true });
    try {
        const page = await browser.newPage();
        const markup = fs.readFileSync(path.join(root, "static/dashboard.html"), "utf8")
            .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, "");
        await page.setContent(markup);
        await page.addScriptTag({ path: path.join(root, "static/js/template-layout.js") });
        await page.addScriptTag({ path: path.join(root, "static/js/signature-pad.js") });
        const result = await page.evaluate(() => {
            const host = document.getElementById("single-signature-host");
            document.body.appendChild(host);
            const editor = window.SignaturePad.mountGroup(host);
            const layout = { version: 2, elements: [
                ...["chair", "dean", "witness"].map((id) => window.TemplateLayout.createElement({ kind: "signature", id, label: id })),
                window.TemplateLayout.createElement({ kind: "signature", id: "hidden", visible: false }),
            ] };
            editor.setLayout(layout, 1);
            const fields = Array.from(host.querySelectorAll("fieldset"));
            const controlIds = Array.from(host.querySelectorAll("[id]"), control => control.id);
            fields.forEach((field, index) => {
                field.querySelector('[data-signature-role="name"]').value = `Signer ${index}`;
                field.querySelector('[data-signature-role="position"]').value = `Position ${index}`;
                const canvas = field.querySelector("canvas");
                const context = canvas.getContext("2d");
                context.fillRect(5 + index, 5, 20, 10);
                canvas.dispatchEvent(new PointerEvent("pointerdown", { clientX: 1, clientY: 1 }));
                canvas.dispatchEvent(new PointerEvent("pointerup", { clientX: 1, clientY: 1 }));
            });
            const submitted = editor.submission();
            editor.setLayout(layout, 1);
            const retained = editor.submission();
            fields[1].querySelector('[data-signature-role="clear"]').click();
            let missing;
            try { editor.submission(); } catch (error) { missing = error.code; }
            const focusedId = document.activeElement.id;
            editor.setLayout(layout, 2);
            const clearedOnSwitch = Array.from(host.querySelectorAll('input[data-signature-role="name"]'), input => input.value);
            editor.setLayout({ version: 2, elements: [] }, 3);
            return {
                fieldCount: fields.length, controlCount: controlIds.length, uniqueIds: new Set(controlIds).size,
                submitted, retained, missing, focusedId, clearedOnSwitch,
                hidden: host.hidden, emptySubmission: editor.submission(),
            };
        });
        assert.equal(result.fieldCount, 3);
        assert.equal(result.controlCount, result.uniqueIds);
        assert.deepEqual(Object.keys(result.submitted), ["chair", "dean", "witness"]);
        assert.equal(result.submitted.dean.signer_name, "Signer 1");
        assert.notEqual(result.submitted.chair.png_data_url, result.submitted.witness.png_data_url);
        assert.deepEqual(result.retained, result.submitted);
        assert.equal(result.missing, "SIGNATURE_REQUIRED");
        assert.match(result.focusedId, /dean-name$/);
        assert.deepEqual(result.clearedOnSwitch, ["", "", ""]);
        assert.equal(result.hidden, true);
        assert.equal(result.emptySubmission, null);
        console.log("Browser signature editors passed: independent drawing, IDs, validation, draft retention, template reset, hidden blocks.");

        for (const width of [1440, 390]) {
            await page.setViewportSize({ width, height: 900 });
            await page.setContent(markup);
            for (const file of ["style.css", "dashboard.css"]) {
                await page.addStyleTag({ path: path.join(root, "static/css", file) });
            }
            await page.addScriptTag({ path: path.join(root, "static/js/template-layout.js") });
            await page.addScriptTag({ path: path.join(root, "static/js/signature-pad.js") });
            await page.evaluate(() => {
                const host = document.getElementById("single-signature-host");
                const template = document.getElementById("signature-editor-template");
                const heading = document.createElement("h1");
                heading.textContent = "ลายเซ็นผู้ลงนาม";
                document.body.replaceChildren(heading, host, template);
                document.body.style.cssText = "margin:0;padding:16px;max-width:900px";
                const editor = window.SignaturePad.mountGroup(host);
                editor.setLayout({ version: 2, elements: ["ประธานกรรมการ", "คณบดี", "พยาน"].map((label, index) =>
                    window.TemplateLayout.createElement({ kind: "signature", id: `signature_${index}`, label })) }, 1);
            });
            const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
            assert.equal(overflow, false, `signature editors overflow at ${width}px`);
            fs.mkdirSync(path.join(root, "test-artifacts/signatures"), { recursive: true });
            await page.screenshot({ path: path.join(root, `test-artifacts/signatures/editors-${width}.png`), fullPage: true });
        }
        console.log("Responsive signature editors passed at 1440px and 390px without horizontal overflow.");
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
