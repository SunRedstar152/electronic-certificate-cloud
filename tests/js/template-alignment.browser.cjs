// Requires Playwright and Microsoft Edge: node tests/js/template-alignment.browser.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
    const root = path.resolve(__dirname, "../..");
    const browser = await chromium.launch({ channel: "msedge", headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
        const errors = [];
        page.on("pageerror", error => errors.push(error.message));
        await page.setContent(fs.readFileSync(path.join(root, "static/dashboard.html"), "utf8")
            .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, ""));
        for (const name of ["style.css", "dashboard.css"]) {
            await page.addStyleTag({ path: path.join(root, "static/css", name) });
        }
        await page.addScriptTag({ path: path.join(root, "static/js/template-layout.js") });
        await page.addScriptTag({ content: fs.readFileSync(path.join(root, "static/js/dashboard.js"), "utf8")
            .replace("    bootstrap();", `
                window.previewRequests = 0;
                scheduleTemplatePreview = () => { window.previewRequests += 1; };
                window.editorTest = { state, wireEvents, renderTemplateEditor, renderTemplateProperties, renderRealTemplatePreview };
            `) });
        await page.evaluate(() => {
            const editor = window.editorTest;
            editor.wireEvents();
            editor.state.templateLayout = { version: 2, elements: [
                window.TemplateLayout.createElement({ id: "name", source: "recipient_name", label: "ชื่อผู้รับ", box: { x: 0.1, y: 0.2, width: 0.4, height: 0.1 } }),
                window.TemplateLayout.createElement({ id: "signature", kind: "signature", label: "ผู้ลงนาม", box: { x: 0.6, y: 0.7, width: 0.3, height: 0.2 } }),
            ] };
            editor.state.templateSelectedElementId = "name";
            editor.state.templateEditingDimensions = { width: 1000, height: 700 };
            document.getElementById("template-preview-image").src = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="700"><rect width="1000" height="700" fill="#fff7df"/><rect x="35" y="35" width="930" height="630" fill="none" stroke="#dca53d" stroke-width="4"/></svg>');
            document.getElementById("template-empty-preview").hidden = true;
            editor.renderTemplateEditor();
            editor.renderTemplateProperties();
            document.getElementById("template-dialog").showModal();
        });
        assert.equal(await page.locator("[data-template-center]").count(), 3, "center controls must be available");
        const box = () => page.evaluate(() => window.editorTest.state.templateLayout.elements[0].box);
        await page.locator('[data-template-center="horizontal"]').click();
        assert.deepEqual(await box(), { x: 0.3, y: 0.2, width: 0.4, height: 0.1 });
        await page.locator('[data-template-center="vertical"]').click();
        assert.deepEqual(await box(), { x: 0.3, y: 0.45, width: 0.4, height: 0.1 });

        async function dragTo(x, y, alt = false) {
            const canvas = await page.locator("#template-canvas").boundingBox();
            const current = await box();
            const start = { x: canvas.x + (current.x + current.width / 2) * canvas.width, y: canvas.y + (current.y + current.height / 2) * canvas.height };
            if (alt) await page.keyboard.down("Alt");
            await page.mouse.move(start.x, start.y);
            await page.mouse.down();
            await page.mouse.move(canvas.x + (x + current.width / 2) * canvas.width, canvas.y + (y + current.height / 2) * canvas.height, { steps: 8 });
            return async () => { await page.mouse.up(); if (alt) await page.keyboard.up("Alt"); };
        }
        let release = await dragTo(0.302, 0.3);
        assert.ok(Math.abs((await box()).x - 0.3) < 1e-6);
        assert.equal(await page.locator("#template-guide-x").isVisible(), true);
        await release();
        assert.equal(await page.locator("#template-guide-x").isVisible(), false);
        assert.equal(await page.evaluate(() => window.editorTest.state.templateDrag), null);

        release = await dragTo(0.306, 0.35, true);
        assert.ok((await box()).x > 0.303, "Alt must bypass center snapping");
        assert.equal(await page.locator("#template-guide-x").isVisible(), false);
        await release();

        // Many mouse moves must continue to reach the captured drag, including outside the block.
        release = await dragTo(0.05, 0.6);
        assert.ok(Math.abs((await box()).x - 0.05) < 0.003);
        await release();
        await page.locator('[data-template-center="both"]').click();
        assert.deepEqual(await box(), { x: 0.3, y: 0.45, width: 0.4, height: 0.1 });
        const marker = page.locator('[data-element-id="name"]');
        await marker.focus();
        await page.keyboard.press("ArrowRight");
        await page.keyboard.press("ArrowRight");
        assert.ok(Math.abs((await box()).x - 0.32) < 1e-6, "keyboard focus must survive repeated moves");
        await page.evaluate(async () => {
            document.getElementById("template-id").value = "1";
            window.api = { token: () => "test-token" };
            const canvas = document.createElement("canvas");
            const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
            window.fetch = async () => ({ ok: true, blob: async () => blob });
            await window.editorTest.renderRealTemplatePreview();
        });
        await page.keyboard.press("ArrowRight");
        assert.ok(Math.abs((await box()).x - 0.33) < 1e-6, "preview completion must preserve keyboard focus");
        assert.ok(await page.evaluate(() => window.previewRequests >= 7));

        await page.locator("#template-snap-centers").uncheck();
        release = await dragTo(0.305, 0.4);
        assert.ok((await box()).x > 0.302, "the checkbox must disable snapping");
        await release();
        await page.locator("#template-snap-centers").check();

        const beforeResize = await box();
        const canvas = await page.locator("#template-canvas").boundingBox();
        const handle = await marker.locator("[data-resize-handle]").boundingBox();
        await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
        await page.mouse.down();
        await page.mouse.move(handle.x + handle.width / 2 + 0.08 * canvas.width, handle.y + handle.height / 2 + 0.1 * canvas.height, { steps: 5 });
        await page.mouse.up();
        const resized = await box();
        assert.equal(resized.x, beforeResize.x);
        assert.equal(resized.y, beforeResize.y);
        assert.ok(Math.abs(resized.width - 0.48) < 0.003);
        assert.ok(Math.abs(resized.height - 0.2) < 0.003);

        assert.equal(await page.locator("#template-zoom-original").count(), 0);
        await page.locator('[data-template-center="both"]').click();
        const centered = await box();
        assert.ok(Math.abs(centered.x + centered.width / 2 - 0.5) < 1e-9);
        assert.ok(Math.abs(centered.y + centered.height / 2 - 0.5) < 1e-9);
        assert.equal(await page.locator("#template-zoom-fit").count(), 0);
        release = await dragTo(0.1, 0.3);
        await page.evaluate(() => document.getElementById("template-element-layer").dispatchEvent(
            new PointerEvent("pointercancel", { bubbles: true, pointerId: window.editorTest.state.templateDrag.pointerId }),
        ));
        assert.equal(await page.evaluate(() => window.editorTest.state.templateDrag), null);
        await release();

        for (const width of [1440, 390]) {
            await page.setViewportSize({ width, height: 1000 });
            await page.locator('[data-template-center="both"]').click();
            const overflow = await page.locator("#template-dialog").evaluate(dialog => dialog.scrollWidth > dialog.clientWidth);
            assert.equal(overflow, false, `dialog overflow at ${width}px`);
            await page.locator("#template-dialog").evaluate(dialog => { dialog.scrollTop = 0; });
            fs.mkdirSync(path.join(root, "test-artifacts/alignment"), { recursive: true });
            await page.screenshot({ path: path.join(root, `test-artifacts/alignment/editor-${width}.png`) });
        }
        assert.deepEqual(errors, []);
        console.log("Template alignment browser checks passed: centering, guides, free movement, drag/resize, cancellation, keyboard movement, and desktop/mobile layout.");
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
