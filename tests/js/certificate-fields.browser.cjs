// Run with Playwright installed: node tests/js/certificate-fields.browser.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
    const root = path.resolve(__dirname, "../..");
    const browser = await chromium.launch({ channel: "msedge", headless: true });
    try {
        for (const width of [390, 1440]) {
            const page = await browser.newPage({ viewport: { width, height: 900 } });
            const markup = fs.readFileSync(path.join(root, "static/dashboard.html"), "utf8")
                .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, "");
            await page.setContent(markup);
            await page.addScriptTag({ path: path.join(root, "static/js/template-layout.js") });
            await page.addScriptTag({ content: fs.readFileSync(path.join(root, "static/js/dashboard.js"), "utf8")
                .replace("    bootstrap();", "    window.testing = { state, renderSingleTemplateFields, singleIssuancePayload };") });
            await page.evaluate(() => {
                const form = document.getElementById("single-form");
                document.body.replaceChildren(form);
                const field = window.TemplateLayout.createElement({
                    id: "period", source: "custom.period", kind: "date_range", label: "ช่วงวัน", required: false,
                });
                window.testing.state.templates = [{ id: 1, layout: { version: 2, elements: [field] } }];
                document.getElementById("single-template").replaceChildren(new Option("ต้นฉบับ", "1"));
                document.getElementById("single-name").value = "ผู้รับ";
                document.getElementById("single-topic").value = "กิจกรรม";
                document.getElementById("single-date").value = "2026-09-10";
                document.getElementById("single-confirm").checked = true;
                window.testing.renderSingleTemplateFields();
                window.submitCount = 0;
                form.addEventListener("submit", event => { event.preventDefault(); window.submitCount += 1; });
            });
            async function setRange(start, end, eventName = "input") {
                return page.evaluate(({ start, end, eventName }) => {
                    const inputs = Array.from(document.querySelectorAll("#single-custom-fields input"));
                    inputs.forEach((input, i) => { input.value = [start, end][i]; });
                    inputs.forEach(input => input.dispatchEvent(new Event(eventName, { bubbles: true })));
                    const before = window.submitCount;
                    document.getElementById("single-form").requestSubmit();
                    return {
                        submitted: window.submitCount > before,
                        messages: inputs.map(input => input.validationMessage),
                        hint: document.querySelector("[data-range-error]")?.textContent || "",
                        fields: window.testing.singleIssuancePayload().custom_fields,
                    };
                }, { start, end, eventName });
            }
            assert.equal((await setRange("", "")).submitted, true);
            const reversed = await setRange("2026-09-12", "2026-09-10");
            assert.equal(reversed.submitted, false, "A fully filled reversed range must stop before issuance");
            assert.match(reversed.messages[1], /สิ้นสุด/);
            assert.match(reversed.hint, /ช่วงวัน/);
            for (const [start, end] of [["2026-09-10", "2026-09-10"], ["2026-12-31", "2027-01-01"]]) {
                const valid = await setRange(start, end, "change");
                assert.equal(valid.submitted, true);
                assert.equal(valid.hint, "");
                assert.deepEqual(valid.fields, { period: { start, end } });
            }
            for (const [start, end] of [["2026-09-10", ""], ["", "2026-09-10"]]) {
                const partial = await setRange(start, end);
                assert.equal(partial.submitted, false);
                assert.match(partial.hint, /ครบ/);
            }
            const cleared = await setRange("", "");
            assert.equal(cleared.submitted, true);
            assert.deepEqual(cleared.fields, {});
            await page.evaluate(() => {
                window.testing.state.templates[0].layout.elements[0].required = true;
                window.testing.renderSingleTemplateFields();
            });
            assert.equal((await setRange("", "")).submitted, false);
            await page.evaluate(() => {
                const field = window.testing.state.templates[0].layout.elements[0];
                field.kind = "time_range";
                field.required = false;
                window.testing.renderSingleTemplateFields();
            });
            assert.equal((await setRange("17:00", "09:00")).submitted, false);
            assert.equal((await setRange("09:00", "17:00")).submitted, true);
            assert.equal((await setRange("09:00", "09:00")).submitted, true);
            console.log(`Certificate range form passed at ${width}px: reversed, same-day, across years, partial, cleared, required, time ranges.`);
            await page.close();
        }
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
