(function organizationAdminModule(root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.OrganizationAdmin = api;
    }
}(typeof window !== "undefined" ? window : globalThis, function createOrganizationAdminApi() {
    "use strict";

    const MODES = new Set(["new", "personnel", "edit"]);

    function trimmed(value) {
        return String(value || "").trim();
    }

    function dialogMode(mode) {
        if (!MODES.has(mode)) {
            throw new Error("Unknown organization dialog mode.");
        }
        return {
            selectorHidden: mode === "edit",
            organizationFieldsHidden: mode === "personnel",
            personnelFieldsHidden: mode === "edit",
            organizationRequired: mode !== "personnel",
            personnelRequired: mode !== "edit",
        };
    }

    function organizationPayload(organization) {
        return {
            name: trimmed(organization.name),
            contact_name: trimmed(organization.contact_name) || null,
            contact_email: trimmed(organization.contact_email) || null,
            contact_phone: trimmed(organization.contact_phone) || null,
        };
    }

    function personnelPayload(personnel) {
        return {
            username: trimmed(personnel.username),
            password: String(personnel.password || ""),
        };
    }

    function personnelPath(organizationId) {
        return `/admin/organizations/${organizationId}/users`;
    }

    function passwordResetPath(organizationId, userId) {
        return `${personnelPath(organizationId)}/${userId}/reset-password`;
    }

    function saveRequest({ editingId, selectedId, organization, personnel }) {
        const organizationData = organizationPayload(organization || {});
        const personnelData = personnelPayload(personnel || {});
        if (editingId) {
            return { method: "PATCH", url: `/admin/organizations/${editingId}`, body: organizationData };
        }
        if (selectedId === "new") {
            return {
                method: "POST",
                url: "/admin/organizations",
                body: { ...organizationData, ...personnelData },
            };
        }
        return { method: "POST", url: personnelPath(selectedId), body: personnelData };
    }

    function organizationOptions(items) {
        return (Array.isArray(items) ? items.slice() : []).sort((left, right) => {
            const nameComparison = String(left.organization.name).localeCompare(
                String(right.organization.name),
                "th",
            );
            return nameComparison || (Number(left.organization.id) - Number(right.organization.id));
        });
    }

    return Object.freeze({
        dialogMode,
        organizationOptions,
        passwordResetPath,
        personnelPath,
        saveRequest,
    });
}));
