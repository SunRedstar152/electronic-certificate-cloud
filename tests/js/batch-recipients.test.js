const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

class Element {
    constructor() { this.children = []; this.value = ''; this.textContent = ''; }
    appendChild(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    addEventListener(name, callback) { this[name] = callback; }
    setAttribute() {}
    showModal() { this.open = true; }
}
function setup(request) {
    const elements = {};
    const document = {getElementById: () => ({}), createElement: () => new Element(),
        querySelector: key => elements[key] ||= new Element()};
    const window = {api: {request}};
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/dashboard.js'), 'utf8')
        .replace('    bootstrap();', '    window.testing = {openBatchRecipients};');
    vm.runInNewContext(source, {document, window, location: new URL('https://example.test/dashboard'), URLSearchParams});
    return {open: window.testing.openBatchRecipients, elements};
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const text = node => [node.textContent, ...node.children.map(text)].join(' ');

test('recipient dialog shows names, revocation, deletion and bounded search', async () => {
    const calls = [];
    const app = setup(async url => {
        calls.push(url);
        return {total: 3, items: [
            {row_number: 1, recipient_name: 'ผู้รับหนึ่ง', status: 'success', certificate_status: 'active', verification_code: 'ABC'},
            {row_number: 2, recipient_name: 'ผู้รับสอง', status: 'success', certificate_status: 'revoked', verification_code: 'DEF'},
            {row_number: 3, recipient_name: 'ผู้รับสาม', status: 'success', verification_code: null},
        ]};
    });
    app.open({id: 42, topic: 'กิจกรรม'});
    await settle();
    const rows = app.elements['#batch-recipients-body'].children;
    assert.match(text(rows[0]), /ผู้รับหนึ่ง.*ดาวน์โหลด/);
    assert.match(text(rows[1]), /เพิกถอนแล้ว/);
    assert.doesNotMatch(text(rows[1]), /ดาวน์โหลด/);
    assert.match(text(rows[2]), /ใบถูกลบ/);
    app.elements['#batch-recipients-search'].value = 'ชื่อ %';
    app.elements['#batch-recipients-search-form'].onsubmit({preventDefault() {}});
    await settle();
    const url = new URL(calls[1], 'https://example.test');
    assert.equal(url.pathname, '/batches/42/recipients');
    assert.equal(url.searchParams.get('search'), 'ชื่อ %');
    assert.equal(url.searchParams.get('limit'), '10');
});

test('a response for the previous batch cannot overwrite the newly opened batch', async () => {
    const pending = [];
    const app = setup(() => new Promise(resolve => pending.push(resolve)));
    app.open({id: 1, topic: 'old'});
    app.elements['#batch-recipients-dialog'].open = false;
    app.open({id: 2, topic: 'new'});
    pending[1]({total: 0, items: []});
    await settle();
    pending[0]({total: 999, items: []});
    await settle();
    assert.equal(app.elements['#batch-recipients-title'].textContent, 'รายชื่อกลุ่ม #2 · new');
    assert.equal(app.elements['#batch-recipients-state'].textContent, 'ไม่พบรายชื่อตามเงื่อนไข');
});
