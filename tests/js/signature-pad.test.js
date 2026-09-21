const test = require("node:test");
const assert = require("node:assert/strict");
const signaturePad = require("../../static/js/signature-pad.js");

test("PNG detection checks the eight-byte file signature", () => {
    assert.equal(signaturePad.hasPngSignature(
        new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]),
    ), true);
    assert.equal(signaturePad.hasPngSignature(
        new Uint8Array([255, 216, 255, 224, 0, 16, 74, 70]),
    ), false);
});


test("pixel analysis requires transparency and visible ink", () => {
    assert.deepEqual(
        signaturePad.analyzePixels(new Uint8ClampedArray([
            0, 0, 0, 0,
            20, 20, 20, 255,
        ])),
        { hasTransparency: true, hasVisibleInk: true },
    );
    assert.equal(signaturePad.analyzePixels(new Uint8ClampedArray([0, 0, 0, 0])).hasVisibleInk, false);
    assert.equal(signaturePad.analyzePixels(new Uint8ClampedArray([0, 0, 0, 255])).hasTransparency, false);
});

test("canvas points scale pointer coordinates to backing pixels", () => {
    assert.deepEqual(
        signaturePad.canvasPoint(
            { clientX: 150, clientY: 70 },
            { left: 50, top: 20, width: 200, height: 100 },
            { width: 1200, height: 400 },
        ),
        { x: 600, y: 200 },
    );
});

test("submission trims text and never includes editor-only state", () => {
    assert.deepEqual(signaturePad.buildSubmission({
        pngDataUrl: "data:image/png;base64,AAAA",
        signerName: "  ผู้ลงนาม  ",
        signerPosition: "  คณบดี  ",
    }, true), {
        png_data_url: "data:image/png;base64,AAAA",
        signer_name: "ผู้ลงนาม",
        signer_position: "คณบดี",
    });
});

test("required submission reports the first missing field with a stable code", () => {
    assert.throws(
        () => signaturePad.buildSubmission({ pngDataUrl: "", signerName: "", signerPosition: "" }, true),
        (error) => error.code === "SIGNATURE_REQUIRED",
    );
    assert.equal(signaturePad.buildSubmission({}, false), null);
});
