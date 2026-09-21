const test = require("node:test");
const assert = require("node:assert/strict");

const organizationAdmin = require("../../static/js/organization-admin.js");

test("dialog modes expose only the fields needed for each organization workflow", () => {
    assert.deepEqual(organizationAdmin.dialogMode("new"), {
        selectorHidden: false,
        organizationFieldsHidden: false,
        personnelFieldsHidden: false,
        organizationRequired: true,
        personnelRequired: true,
    });
    assert.deepEqual(organizationAdmin.dialogMode("personnel"), {
        selectorHidden: false,
        organizationFieldsHidden: true,
        personnelFieldsHidden: false,
        organizationRequired: false,
        personnelRequired: true,
    });
    assert.deepEqual(organizationAdmin.dialogMode("edit"), {
        selectorHidden: true,
        organizationFieldsHidden: false,
        personnelFieldsHidden: true,
        organizationRequired: true,
        personnelRequired: false,
    });
    assert.throws(() => organizationAdmin.dialogMode("unknown"), /Unknown organization dialog mode/);
});

test("save requests target organization creation, personnel creation, and editing separately", () => {
    const organization = {
        name: "  สำนักวิทยบริการ  ",
        contact_name: "  สมชาย  ",
        contact_email: "  library@example.test  ",
        contact_phone: "  0123456789  ",
    };
    const personnel = { username: "  librarian  ", password: "password-for-initial-account" };

    assert.deepEqual(organizationAdmin.saveRequest({ selectedId: "new", organization, personnel }), {
        method: "POST",
        url: "/admin/organizations",
        body: {
            name: "สำนักวิทยบริการ",
            contact_name: "สมชาย",
            contact_email: "library@example.test",
            contact_phone: "0123456789",
            username: "librarian",
            password: "password-for-initial-account",
        },
    });
    assert.deepEqual(organizationAdmin.saveRequest({ selectedId: "42", organization, personnel }), {
        method: "POST",
        url: "/admin/organizations/42/users",
        body: { username: "librarian", password: "password-for-initial-account" },
    });
    assert.deepEqual(organizationAdmin.saveRequest({ editingId: "42", organization, personnel }), {
        method: "PATCH",
        url: "/admin/organizations/42",
        body: {
            name: "สำนักวิทยบริการ",
            contact_name: "สมชาย",
            contact_email: "library@example.test",
            contact_phone: "0123456789",
        },
    });
});

test("organization options include inactive organizations in Thai name order", () => {
    const options = organizationAdmin.organizationOptions([
        { organization: { id: 3, name: "วิทยาลัยนานาชาติ", status: "inactive" } },
        { organization: { id: 1, name: "คณะวิทยาศาสตร์", status: "active" } },
        { organization: { id: 2, name: "คณะมนุษยศาสตร์", status: "inactive" } },
    ]);

    assert.deepEqual(options.map((item) => item.organization.id), [2, 1, 3]);
    assert.equal(options.some((item) => item.organization.status === "inactive"), true);
});

test("organization options use ascending IDs when Thai names compare equal", () => {
    const options = organizationAdmin.organizationOptions([
        { organization: { id: 22, name: "คณะเดียวกัน", status: "active" } },
        { organization: { id: 7, name: "คณะเดียวกัน", status: "inactive" } },
    ]);

    assert.deepEqual(options.map((item) => item.organization.id), [7, 22]);
});

test("personnel status and password routes are scoped to the selected user", () => {
    assert.equal(organizationAdmin.personnelPath(12), "/admin/organizations/12/users");
    assert.equal(organizationAdmin.passwordResetPath(12, 34), "/admin/organizations/12/users/34/reset-password");
});
