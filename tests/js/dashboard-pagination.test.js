const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
function setup(request) {
    const inputs = {"#certificate-search": {value:""}, "#certificate-status":{value:""}, "#batch-search":{value:""}, "#batch-status":{value:""}};
    const context = {location: new URL("https://example.test/dashboard"), URLSearchParams, setTimeout, clearTimeout, document:{getElementById:()=>({}),querySelector:key=>inputs[key]},window:{api:{request}}};
    const source=fs.readFileSync(path.join(__dirname,"../../static/js/dashboard.js"),"utf8").replace("    bootstrap();", "    window.testing = {state, fetchCertificates, fetchBatches};");
    vm.runInNewContext(source,context);
    return {...context.window.testing,inputs};
}
test("loads only the selected page including pages beyond 100 records", async()=>{
    const calls=[];
    const app=setup(async url=>{calls.push(url);return {total:10000,items:[{id:121}]};});
    app.state.certificatePage=13;
    app.inputs["#certificate-search"].value="ทดสอบ %";
    app.inputs["#certificate-status"].value="revoked";
    await app.fetchCertificates(true);
    assert.equal(calls.length,1);
    const url=new URL(calls[0],"https://example.test");
    assert.equal(url.searchParams.get("offset"),"120");
    assert.equal(url.searchParams.get("limit"),"10");
    assert.equal(url.searchParams.get("search"),"ทดสอบ %");
    assert.equal(url.searchParams.get("status"),"revoked");
    assert.equal(app.state.certificates.length,1);
    assert.equal(app.state.certificateTotal,10000);
});
test("older responses cannot replace a more recent search",async()=>{
    const pending=[];
    const app=setup(()=>new Promise(resolve=>pending.push(resolve)));
    const older=app.fetchCertificates(true);
    const newer=app.fetchCertificates(true);
    pending[1]({total:1,items:[{id:2}]}); await newer;
    pending[0]({total:99,items:[{id:1}]}); await older;
    assert.equal(app.state.certificates[0].id,2);
    assert.equal(app.state.certificateTotal,1);
});
test("batch history requests one bounded page",async()=>{
    const calls=[];const app=setup(async url=>{calls.push(url);return {total:40,items:[]};});
    app.state.batchPage=3;await app.fetchBatches(true);
    assert.equal(calls.length,1);
    assert.equal(new URL(calls[0],"https://example.test").searchParams.get("offset"),"20");
    assert.equal(app.state.batchTotal,40);
});
