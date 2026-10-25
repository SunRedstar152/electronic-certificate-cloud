(function initializeDashboard() {
    "use strict";

    if (!document.getElementById("dashboard-content")) {
        return;
    }

    const isEditorPage = location.pathname === "/template-editor";
    let editorBaseline = null;
    let editorSaved = false;
    if (isEditorPage) document.body.classList.add("template-editor-page");
    const editorSnapshot = () => JSON.stringify([document.getElementById("template-name").value, state.templateLayout]);
    function editorHasChanges() {
        return !editorSaved && editorBaseline !== null && (editorSnapshot() !== editorBaseline || document.getElementById("template-file").files.length > 0);
    }
    function leaveEditor() {
        if (editorHasChanges() && !window.confirm("ยังไม่ได้บันทึกการแก้ไข ต้องการออกจากหน้านี้หรือไม่?")) return;
        editorSaved = true;
        location.assign("/dashboard?view=templates");
    }
    function setupEditorPage() {
        document.title = "ออกแบบแม่แบบ | PKRU Electronic Certificate";
        const old = document.getElementById("template-dialog");
        const page = document.createElement("main");
        page.id = old.id;
        page.className = "template-editor-page-main";
        page.setAttribute("aria-labelledby", "template-dialog-title");
        page.append(...old.childNodes);
        old.replaceWith(page);
        const heading = page.querySelector(".dialog-heading");
        const back = heading.querySelector("[data-close-dialog]");
        back.textContent = "← กลับไปแม่แบบ";
        back.className = "button button-secondary";
        back.setAttribute("aria-label", "กลับไปหน้าแม่แบบ");
        heading.prepend(back);
        const save = document.getElementById("template-save-button");
        heading.append(save);
        page.querySelector(".form-actions").remove();
        heading.after(document.getElementById("global-alert"));
        document.querySelector(".skip-link").href = "#template-dialog";
        window.addEventListener("beforeunload", event => {
            if (editorHasChanges()) { event.preventDefault(); event.returnValue = ""; }
        });
        window.addEventListener("resize", () => { if (state.templateLayout) applyTemplateZoom(); });
    }
    const PAGE_SIZE = 10;
    const CERTIFICATE_API_PAGE_SIZE = 100;
    const MAX_RECIPIENT_VALUE_LENGTH = 255;
    const state = {
        user: null,
        view: null,
        templates: [],
        batches: [],
        certificates: [],
        organizations: [],
        organizationOptions: [],
        organizationOptionsLoaded: false,
        organizationDialogMode: "new",
        organizationPage: 1,
        organizationTotal: 0,
        auditPage: 1,
        auditTotal: 0,
        batchPage: 1,
        certificatePage: 1,
        bulkStep: 1,
        bulkRows: [],
        bulkBatch: null,
        singleIssuanceAttempt: null,
        signatureEditors: { single: null, bulk: null, dialog: null },
        signatureResolve: null,
        confirmResolve: null,
        confirmRequiresInput: false,
        templateObjectUrl: null,
        templatePositions: {},
        templateDrag: null,
        templateLayout: { version: 2, elements: [] },
        templateSelectedElementId: null,
        templateZoom: "fit",
        templateFonts: [],
        templatePreviewController: null,
        templatePreviewTimer: null,
        templatePreviewErrorId: null,
    };

    const $ = (selector, root) => (root || document).querySelector(selector);
    const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));

    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) {
            element.className = className;
        }
        if (text !== undefined && text !== null) {
            element.textContent = String(text);
        }
        return element;
    }

    function append(parent, children) {
        children.filter(Boolean).forEach((child) => parent.appendChild(child));
        return parent;
    }

    function clear(element) {
        element.replaceChildren();
    }

    function formatDate(value, includeTime) {
        if (!value) {
            return "—";
        }
        const date = new Date(value.length === 10 ? `${value}T00:00:00` : value);
        if (Number.isNaN(date.getTime())) {
            return value;
        }
        return new Intl.DateTimeFormat("th-TH", includeTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" }).format(date);
    }

    function statusBadge(status) {
        const labels = {
            active: ["ใช้งาน", "badge-success"],
            inactive: ["ไม่ใช้งาน", "badge-danger"],
            revoked: ["เพิกถอน", "badge-danger"],
            draft: ["แบบร่าง", "badge-info"],
            validated: ["รอประมวลผล", "badge-warning"],
            processing: ["กำลังประมวลผล", "badge-info"],
            completed: ["สำเร็จ", "badge-success"],
            partial: ["สำเร็จบางส่วน", "badge-warning"],
            failed: ["ล้มเหลว", "badge-danger"],
            pending: ["รอดำเนินการ", "badge-warning"],
            success: ["สำเร็จ", "badge-success"],
        };
        const config = labels[status] || [status || "—", ""];
        return node("span", `badge ${config[1]}`, config[0]);
    }

    function actionButton(label, handler, danger) {
        const button = node("button", `action-button${danger ? " action-button-danger" : ""}`, label);
        button.type = "button";
        button.addEventListener("click", handler);
        return button;
    }

    function showGlobalError(error) {
        const alert = $("#global-alert");
        $("#global-error-code").textContent = error.code || "HTTP_ERROR";
        $("#global-error-message").textContent = error.message || "The request could not be completed.";
        alert.hidden = false;
        alert.scrollIntoView({ block: "nearest" });
    }

    function hideGlobalError() {
        $("#global-alert").hidden = true;
    }

    function toast(message, kind) {
        const item = node("div", `toast toast-${kind || "info"}`, message);
        $("#toast-region").appendChild(item);
        window.setTimeout(() => item.remove(), 4200);
    }

    const SIGNATURE_MESSAGES = {
        SIGNATURE_REQUIRED: "กรุณาใส่ลายเซ็น ชื่อ และตำแหน่งผู้ลงนาม",
        SIGNATURE_INVALID_PNG: "ไฟล์ลายเซ็นไม่ใช่ PNG ที่ถูกต้อง",
        SIGNATURE_TOO_LARGE: "ไฟล์ลายเซ็นมีขนาดใหญ่เกินกำหนด",
        SIGNATURE_TRANSPARENCY_REQUIRED: "กรุณาใช้ PNG พื้นหลังโปร่งใส หรือวาดลายเซ็นในระบบ",
        SIGNATURE_EMPTY: "กรุณาวาดหรือเลือกภาพลายเซ็นที่ไม่ว่าง",
        SIGNATURE_TEXT_OVERFLOW: "ชื่อหรือตำแหน่งไม่พอดีกับกรอบลายเซ็นในแม่แบบ",
        SIGNATURE_UNEXPECTED: "ข้อมูลลายเซ็นไม่ตรงกับบล็อกลายเซ็นในแม่แบบ",
    };

    function apiFailure(error) {
        if (error && SIGNATURE_MESSAGES[error.code]) {
            error = Object.assign(error, { message: SIGNATURE_MESSAGES[error.code] });
        }
        if (error.status === 401) {
            window.api.clearToken();
            window.location.replace("/login");
            return;
        }
        showGlobalError(error);
    }

    function assetUrl(template) {
        if (!template || !Number.isInteger(Number(template.id))) {
            return "";
        }
        return `/templates/${template.id}/background`;
    }

    async function loadTemplateImage(image, template, keepObjectUrl = false) {
        const blob = await window.api.request(assetUrl(template), { responseType: "blob" });
        const objectUrl = URL.createObjectURL(blob);
        const previous = image.dataset.privateObjectUrl;
        if (previous) URL.revokeObjectURL(previous);
        image.dataset.privateObjectUrl = objectUrl;
        image.src = objectUrl;
        if (!keepObjectUrl) {
            image.addEventListener("load", () => {
                URL.revokeObjectURL(objectUrl);
                if (image.dataset.privateObjectUrl === objectUrl) delete image.dataset.privateObjectUrl;
            }, { once: true });
        }
        return objectUrl;
    }

    function pagination(container, page, total, onChange) {
        clear(container);
        const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
        if (pages <= 1) {
            return;
        }
        const previous = node("button", "", "‹");
        previous.type = "button";
        previous.disabled = page <= 1;
        previous.setAttribute("aria-label", "หน้าก่อนหน้า");
        previous.addEventListener("click", () => onChange(page - 1));
        container.appendChild(previous);

        const start = Math.max(1, page - 2);
        const end = Math.min(pages, page + 2);
        if (start > 1) {
            container.appendChild(node("span", "", "…"));
        }
        for (let number = start; number <= end; number += 1) {
            const button = node("button", "", number);
            button.type = "button";
            button.setAttribute("aria-label", `หน้า ${number}`);
            if (number === page) {
                button.setAttribute("aria-current", "page");
            }
            button.addEventListener("click", () => onChange(number));
            container.appendChild(button);
        }
        if (end < pages) {
            container.appendChild(node("span", "", "…"));
        }
        const next = node("button", "", "›");
        next.type = "button";
        next.disabled = page >= pages;
        next.setAttribute("aria-label", "หน้าถัดไป");
        next.addEventListener("click", () => onChange(page + 1));
        container.appendChild(next);
    }

    function setLoading(container, message) {
        clear(container);
        container.appendChild(node("p", "loading-state", message || "กำลังโหลดข้อมูล…"));
    }

    function updateNavigationAccessibility() {
        const sidebar = $("#sidebar");
        const isClosedMobileNavigation = window.matchMedia("(max-width: 860px)").matches && !sidebar.classList.contains("is-open");
        if (isClosedMobileNavigation) {
            sidebar.inert = true;
            sidebar.setAttribute("aria-hidden", "true");
        } else {
            sidebar.inert = false;
            sidebar.removeAttribute("aria-hidden");
        }
    }

    function openNavigation() {
        const sidebar = $("#sidebar");
        sidebar.classList.add("is-open");
        sidebar.inert = false;
        sidebar.removeAttribute("aria-hidden");
        $("#navigation-scrim").hidden = false;
        $("#open-navigation").setAttribute("aria-expanded", "true");
    }

    function closeNavigation() {
        const sidebar = $("#sidebar");
        const wasOpen = sidebar.classList.contains("is-open");
        sidebar.classList.remove("is-open");
        $("#navigation-scrim").hidden = true;
        $("#open-navigation").setAttribute("aria-expanded", "false");
        updateNavigationAccessibility();
        if (wasOpen) {
            $("#open-navigation").focus();
        }
    }

    async function showView(name) {
        const view = $(`#view-${name}`);
        if (!view) {
            return;
        }
        hideGlobalError();
        $$(".dashboard-view").forEach((item) => { item.hidden = item !== view; });
        $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === name));
        $("#page-title").textContent = view.dataset.title || "พื้นที่จัดการ";
        $("#page-eyebrow").textContent = view.dataset.eyebrow || "DASHBOARD";
        state.view = name;
        closeNavigation();
        $("#dashboard-content").focus({ preventScroll: true });
        try {
            await loadView(name);
        } catch (error) {
            apiFailure(error);
        }
    }

    async function loadView(name) {
        const loaders = {
            "admin-overview": loadAdminOverview,
            organizations: loadOrganizations,
            audit: loadAuditLogs,
            "organization-overview": loadOrganizationOverview,
            templates: loadTemplates,
            single: prepareSingleIssuance,
            bulk: prepareBulkIssuance,
            batches: loadBatches,
            certificates: loadCertificates,
        };
        if (loaders[name]) {
            await loaders[name]();
        }
    }

    async function loadAdminOverview() {
        const stats = await window.api.request("/admin/stats");
        $$("[data-stat]").forEach((element) => {
            const [group, key] = element.dataset.stat.split(".");
            element.textContent = stats[group] && stats[group][key] !== undefined ? stats[group][key] : "0";
        });
        $("[data-stat-note='organizations']").textContent = `${stats.organizations.active} ใช้งาน · ${stats.organizations.inactive} ไม่ใช้งาน`;
        const health = $("#admin-health");
        clear(health);
        const rows = [
            ["ขอบเขตบัญชีหน่วยงาน", `${stats.accounts.active} บัญชีพร้อมใช้งาน`],
            ["ข้อมูลเกียรติบัตร", `${stats.certificates.total} รายการในระบบ`],
            ["งานแบบกลุ่ม", `${stats.batches.total} รายการประมวลผล`],
        ];
        rows.forEach(([title, detail]) => {
            const content = append(node("span"), [node("strong", "", title), node("small", "", detail)]);
            health.appendChild(append(node("div", "status-row"), [node("span", "status-dot"), content, statusBadge("active")]));
        });
    }

    function organizationQuery() {
        const query = new URLSearchParams({ page: String(state.organizationPage), page_size: String(PAGE_SIZE) });
        const search = $("#organization-search").value.trim();
        const status = $("#organization-status").value;
        if (search) {
            query.set("search", search);
        }
        if (status) {
            query.set("status", status);
        }
        return query;
    }

    async function loadOrganizations() {
        const tableBody = $("#organizations-table-body");
        const tableState = $("#organizations-state");
        clear(tableBody);
        tableState.hidden = false;
        tableState.textContent = "กำลังโหลดข้อมูล…";
        const result = await window.api.request(`/admin/organizations?${organizationQuery().toString()}`);
        state.organizations = result.items || [];
        state.organizationTotal = result.total || 0;
        clear(tableBody);
        if (!state.organizations.length) {
            tableState.textContent = "ไม่พบหน่วยงานตามเงื่อนไข";
        } else {
            tableState.hidden = true;
        }

        state.organizations.forEach((item) => {
            const organization = item.organization;
            const row = node("tr");
            const identity = append(node("div", "cell-primary"), [node("strong", "", organization.name)]);
            const personnel = append(node("div", "cell-primary"), [node("span", "", `ใช้งาน ${item.active_personnel_count} คน`), node("small", "", `ทั้งหมด ${item.personnel_count} คน`)]);
            const contact = append(node("div", "cell-primary"), [node("span", "", organization.contact_name || "—"), node("small", "", organization.contact_email || organization.contact_phone || "ไม่มีข้อมูลติดต่อ")]);
            const actions = node("div", "action-group");
            actions.appendChild(actionButton("แก้ไข", () => openOrganizationDialog(item).catch(apiFailure)));
            actions.appendChild(actionButton("จัดการบุคลากร", () => openPersonnelDialog(organization).catch(apiFailure)));
            const deactivating = organization.status === "active";
            actions.appendChild(actionButton(deactivating ? "ปิดใช้งาน" : "เปิดใช้งาน", () => toggleOrganization(item), deactivating));
            [identity, personnel, contact, statusBadge(organization.status), actions].forEach((content) => {
                const cell = node("td");
                cell.appendChild(content);
                row.appendChild(cell);
            });
            tableBody.appendChild(row);
        });
        pagination($("#organizations-pagination"), state.organizationPage, state.organizationTotal, (page) => {
            state.organizationPage = page;
            loadOrganizations().catch(apiFailure);
        });
    }

    async function loadOrganizationOptions() {
        const organizations = [];
        let page = 1;
        let total = 0;
        do {
            const result = await window.api.request(`/admin/organizations?page=${page}&page_size=100`);
            const items = result.items || [];
            organizations.push(...items);
            total = result.total || 0;
            page += 1;
            if (!items.length) break;
        } while (organizations.length < total);
        state.organizationOptions = window.OrganizationAdmin.organizationOptions(organizations);
        state.organizationOptionsLoaded = true;
        populateOrganizationSelector();
        populateAuditOrganizations();
    }

    function populateOrganizationSelector() {
        const select = $("#organization-selector");
        const current = select.value || "new";
        clear(select);
        select.appendChild(new Option("+ สร้างหน่วยงานใหม่", "new"));
        state.organizationOptions.forEach((item) => {
            select.appendChild(new Option(item.organization.name, String(item.organization.id)));
        });
        select.value = Array.from(select.options).some((option) => option.value === current) ? current : "new";
    }

    function setOrganizationDialogMode(mode) {
        const settings = window.OrganizationAdmin.dialogMode(mode);
        state.organizationDialogMode = mode;
        $("#organization-selector-field").hidden = settings.selectorHidden;
        $("#organization-fields").hidden = settings.organizationFieldsHidden;
        $("#personnel-account-fields").hidden = settings.personnelFieldsHidden;
        $("#organization-name").required = settings.organizationRequired;
        $("#organization-username").required = settings.personnelRequired;
        $("#organization-password").required = settings.personnelRequired;
        $("#organization-dialog-title").textContent = mode === "personnel"
            ? "เพิ่มบุคลากรในหน่วยงาน"
            : mode === "edit" ? "แก้ไขหน่วยงาน" : "สร้างหน่วยงานใหม่";
    }

    async function openOrganizationDialog(item, selectedOrganizationId) {
        const editing = Boolean(item);
        const organization = editing ? item.organization : {};
        $("#organization-id").value = editing ? organization.id : "";
        $("#organization-name").value = organization.name || "";
        $("#organization-contact-name").value = organization.contact_name || "";
        $("#organization-email").value = organization.contact_email || "";
        $("#organization-phone").value = organization.contact_phone || "";
        $("#organization-username").value = "";
        $("#organization-password").value = "";
        if (!editing) {
            await loadOrganizationOptions();
            $("#organization-selector").value = selectedOrganizationId ? String(selectedOrganizationId) : "new";
        }
        setOrganizationDialogMode(editing ? "edit" : $("#organization-selector").value === "new" ? "new" : "personnel");
        $("#organization-dialog").showModal();
        $(editing || $("#organization-selector").value === "new" ? "#organization-name" : "#organization-username").focus();
    }

    async function saveOrganization(event) {
        event.preventDefault();
        const form = event.currentTarget;
        if (!form.reportValidity()) {
            return;
        }
        const request = window.OrganizationAdmin.saveRequest({
            editingId: $("#organization-id").value,
            selectedId: $("#organization-selector").value,
            organization: {
                name: $("#organization-name").value,
                contact_name: $("#organization-contact-name").value,
                contact_email: $("#organization-email").value,
                contact_phone: $("#organization-phone").value,
            },
            personnel: {
                username: $("#organization-username").value,
                password: $("#organization-password").value,
            },
        });
        const button = $("#organization-save-button");
        button.disabled = true;
        try {
            await window.api.request(request.url, { method: request.method, body: request.body });
            $("#organization-dialog").close();
            toast(request.method === "PATCH" ? "บันทึกข้อมูลหน่วยงานแล้ว" : "บันทึกบัญชีบุคลากรแล้ว", "success");
            await loadOrganizations();
            await loadOrganizationOptions();
        } catch (error) {
            if (error && error.code === "ORG_NAME_EXISTS") {
                loadOrganizationOptions().catch(apiFailure);
            }
            apiFailure(error);
        } finally {
            button.disabled = false;
        }
    }

    async function toggleOrganization(item) {
        const organization = item.organization;
        const deactivating = organization.status === "active";
        const answer = await requestConfirmation({
            title: deactivating ? "ปิดการใช้งานหน่วยงาน" : "เปิดการใช้งานหน่วยงาน",
            message: `${organization.name} จะ${deactivating ? "ไม่สามารถ" : "สามารถ"}เข้าสู่ระบบและใช้งานข้อมูลได้`,
            actionLabel: deactivating ? "ปิดใช้งาน" : "เปิดใช้งาน",
            danger: deactivating,
        });
        if (!answer.confirmed) {
            return;
        }
        try {
            await window.api.request(`/admin/organizations/${organization.id}`, { method: "PATCH", body: { status: deactivating ? "inactive" : "active" } });
            toast("อัปเดตสถานะหน่วยงานแล้ว", "success");
            await loadOrganizations();
            await loadOrganizationOptions();
        } catch (error) {
            apiFailure(error);
        }
    }

    async function loadPersonnel(organizationId) {
        const tableBody = $("#personnel-table-body");
        const tableState = $("#personnel-state");
        clear(tableBody);
        tableState.hidden = false;
        tableState.textContent = "กำลังโหลดข้อมูล…";
        const result = await window.api.request(window.OrganizationAdmin.personnelPath(organizationId));
        clear(tableBody);
        const accounts = result.items || [];
        if (!accounts.length) {
            tableState.textContent = "ไม่พบบุคลากร";
            return;
        }
        tableState.hidden = true;
        accounts.forEach((account) => {
            const row = node("tr");
            const actions = node("div", "action-group");
            actions.appendChild(actionButton("รหัสผ่าน", () => openPasswordDialog(organizationId, account)));
            const deactivating = account.is_active;
            actions.appendChild(actionButton(deactivating ? "ปิดใช้งาน" : "เปิดใช้งาน", () => togglePersonnel(account), deactivating));
            [node("span", "", account.username), statusBadge(account.is_active ? "active" : "inactive"), actions].forEach((content) => {
                const cell = node("td");
                cell.appendChild(content);
                row.appendChild(cell);
            });
            tableBody.appendChild(row);
        });
    }

    async function openPersonnelDialog(organization) {
        $("#personnel-organization-id").value = organization.id;
        $("#personnel-dialog-title").textContent = `จัดการบุคลากร: ${organization.name}`;
        $("#personnel-dialog").showModal();
        await loadPersonnel(organization.id);
    }

    async function togglePersonnel(account) {
        const organizationId = $("#personnel-organization-id").value;
        const deactivating = account.is_active;
        const answer = await requestConfirmation({
            title: deactivating ? "ปิดใช้งานบุคลากร" : "เปิดใช้งานบุคลากร",
            message: `${account.username} จะ${deactivating ? "ไม่สามารถ" : "สามารถ"}เข้าสู่ระบบได้`,
            actionLabel: deactivating ? "ปิดใช้งาน" : "เปิดใช้งาน",
            danger: deactivating,
        });
        if (!answer.confirmed) return;
        try {
            await window.api.request(`${window.OrganizationAdmin.personnelPath(organizationId)}/${account.id}`, {
                method: "PATCH",
                body: { is_active: !account.is_active },
            });
            toast("อัปเดตสถานะบุคลากรแล้ว", "success");
            await Promise.all([loadPersonnel(organizationId), loadOrganizations()]);
        } catch (error) {
            apiFailure(error);
        }
    }

    function openPasswordDialog(organizationId, account) {
        $("#password-organization-id").value = organizationId;
        $("#password-user-id").value = account.id;
        $("#password-dialog-title").textContent = `ตั้งรหัสผ่านใหม่: ${account.username}`;
        $("#new-password").value = "";
        $("#password-dialog").showModal();
        $("#new-password").focus();
    }

    async function resetPassword(event) {
        event.preventDefault();
        if (!event.currentTarget.reportValidity()) {
            return;
        }
        const organizationId = $("#password-organization-id").value;
        const userId = $("#password-user-id").value;
        try {
            await window.api.request(window.OrganizationAdmin.passwordResetPath(organizationId, userId), { method: "POST", body: { password: $("#new-password").value } });
            $("#password-dialog").close();
            toast("ตั้งรหัสผ่านใหม่แล้ว", "success");
        } catch (error) {
            apiFailure(error);
        }
    }

    function populateAuditOrganizations() {
        const select = $("#audit-organization");
        const current = select.value;
        const first = select.firstElementChild;
        clear(select);
        select.appendChild(first || new Option("ทั้งหมด", ""));
        state.organizationOptions.forEach((item) => select.appendChild(new Option(item.organization.name, item.organization.id)));
        select.value = current;
    }

    function auditQuery() {
        const query = new URLSearchParams({ page: String(state.auditPage), page_size: String(PAGE_SIZE) });
        const values = {
            organization_id: $("#audit-organization").value,
            action: $("#audit-action").value.trim(),
            date_from: $("#audit-from").value ? `${$("#audit-from").value}T00:00:00Z` : "",
            date_to: $("#audit-to").value ? `${$("#audit-to").value}T23:59:59Z` : "",
        };
        Object.entries(values).forEach(([key, value]) => { if (value) query.set(key, value); });
        return query;
    }

    async function loadAuditLogs() {
        if (!state.organizationOptionsLoaded) {
            await loadOrganizationOptions();
        }
        const list = $("#audit-list");
        setLoading(list);
        const result = await window.api.request(`/admin/audit-logs?${auditQuery().toString()}`);
        state.auditTotal = result.total || 0;
        clear(list);
        if (!result.items.length) {
            list.appendChild(node("p", "empty-state", "ไม่พบบันทึกตามเงื่อนไข"));
        }
        result.items.forEach((entry) => {
            const marker = node("span", "audit-marker", String(entry.action || "?").slice(0, 1).toUpperCase());
            const content = node("div", "audit-content");
            const actor = entry.actor ? entry.actor.username : "system";
            content.appendChild(node("p", "monospace", entry.action));
            content.appendChild(node("small", "", `${actor} · ${entry.organization ? entry.organization.name : "ระดับระบบ"} · ${entry.target_type || "—"} #${entry.target_id || "—"}`));
            if (entry.details && Object.keys(entry.details).length) {
                content.appendChild(node("pre", "audit-details", JSON.stringify(entry.details, null, 2)));
            }
            const time = node("time", "", formatDate(entry.created_at, true));
            if (entry.created_at) {
                time.dateTime = entry.created_at;
            }
            list.appendChild(append(node("article", "audit-entry"), [marker, content, time]));
        });
        pagination($("#audit-pagination"), state.auditPage, state.auditTotal, (page) => {
            state.auditPage = page;
            loadAuditLogs().catch(apiFailure);
        });
    }

    async function fetchTemplates(force) {
        if (force || !state.templates.length) {
            state.templates = await window.api.request("/templates");
        }
        return state.templates;
    }

    let batchListRequest = 0;
    let certificateListRequest = 0;
    async function fetchBatches(force) {
        const requestId = ++batchListRequest;
        const params = new URLSearchParams({offset: (state.batchPage - 1) * PAGE_SIZE, limit: PAGE_SIZE,
            search: $("#batch-search").value.trim()});
        if ($("#batch-status").value) params.set("status", $("#batch-status").value);
        const result = await window.api.request(`/dashboard/batches?${params}`);
        if (requestId !== batchListRequest) return;
        state.batches = result.items;
        state.batchTotal = result.total;
        return result.items;
    }

    async function fetchCertificates(force) {
        const requestId = ++certificateListRequest;
        const params = new URLSearchParams({offset: (state.certificatePage - 1) * PAGE_SIZE, limit: PAGE_SIZE,
            search: $("#certificate-search").value.trim()});
        if ($("#certificate-status").value) params.set("status", $("#certificate-status").value);
        const result = await window.api.request(`/dashboard/certificates?${params}`);
        if (requestId !== certificateListRequest) return;
        state.certificates = result.items;
        state.certificateTotal = result.total;
        return result.items;
    }

    async function loadOrganizationOverview() {
        const result = await window.api.request("/dashboard/summary");
        $("#overview-template-count").textContent = result.template_count;
        $("#overview-batch-count").textContent = result.batch_count;
        $("#overview-certificate-count").textContent = result.certificate_count;
        $("#overview-revoked-count").textContent = result.revoked_count;
        const recent = $("#overview-recent-batches");
        clear(recent);
        if (!result.recent_batches.length) recent.appendChild(node("p", "empty-state", "ยังไม่มีรายการประมวลผล"));
        result.recent_batches.forEach((batch) => {
            const content = append(node("span"), [node("strong", "", batch.topic), node("small", "", `${batch.success_count}/${batch.total_count} สำเร็จ`)]);
            recent.appendChild(append(node("div", "status-row"), [node("span", "status-dot"), content, statusBadge(batch.status)]));
        });
    }

    async function loadTemplates() {
        const list = $("#template-list");
        setLoading(list);
        const templates = await fetchTemplates(true);
        renderTemplates(templates);
        populateTemplateSelects();
    }

    function renderTemplates(templates) {
        const list = $("#template-list");
        clear(list);
        if (!templates.length) {
            list.appendChild(node("p", "empty-state", "ยังไม่มีแม่แบบ กด “เพิ่มแม่แบบ” เพื่อเริ่มต้น"));
            return;
        }
        templates.forEach((template) => {
            const imageShell = node("div", "template-image");
            const image = node("img");
            image.alt = `ตัวอย่างแม่แบบ ${template.name}`;
            image.loading = "lazy";
            loadTemplateImage(image, template).catch(apiFailure);
            imageShell.appendChild(image);
            const content = node("div", "template-card-content");
            content.appendChild(node("h3", "", template.name));
            const dimensions = template.dimensions || {};
            const meta = append(node("div", "template-meta"), [node("span", "", `${dimensions.width || "—"} × ${dimensions.height || "—"}`), statusBadge(template.status)]);
            content.appendChild(meta);
            const actions = node("div", "action-group");
            if (template.is_builtin) {
                content.appendChild(node("p", "field-help", "แม่แบบประจำระบบ · พร้อมใช้ทุกหน่วยงาน"));
                actions.appendChild(actionButton("ทำสำเนาเพื่อแก้ไข", () => copyTemplate(template)));
            } else {
                actions.appendChild(actionButton("แก้ไขตำแหน่ง", () => openTemplateDialog(template)));
            }
            if (!template.is_builtin && template.status === "active") {
                actions.appendChild(actionButton("ปิดใช้งาน", () => deactivateTemplate(template), true));
            }
            if (!template.is_builtin) {
                actions.appendChild(actionButton("ลบแม่แบบ", () => deleteTemplate(template), true));
            }
            content.appendChild(actions);
            list.appendChild(append(node("article", "template-card"), [imageShell, content]));
        });
    }

    function defaultPositions() {
        return {
            recipient_name: { x: 0.5, y: 0.43 },
            topic: { x: 0.5, y: 0.56 },
            issue_date: { x: 0.5, y: 0.70 },
            qr_code: { x: 0.85, y: 0.82 },
        };
    }

    function templateTextStyle(size) {
        return { font: { source: "builtin", key: "sarabun" }, font_size_px: size, color: "#111111", weight: 400, align: "center", line_height: 1.25 };
    }

    function defaultTemplateLayout(positions) {
        const sourcePositions = Object.assign(defaultPositions(), positions || {});
        const text = (id, label, size, width, height) => ({
            id, source: id, kind: id === "issue_date" ? "date" : "text", label, required: true, visible: true,
            box: { x: Math.max(0, sourcePositions[id].x - width / 2), y: Math.max(0, sourcePositions[id].y - height / 2), width, height },
            style: templateTextStyle(size),
            ...(id === "issue_date" ? { format: { locale: "th-TH", calendar: "buddhist", pattern: "long", prefix: "", separator: " ถึง ", suffix: "" } } : {}),
            sample_value: id === "recipient_name" ? "ชื่อผู้รับเกียรติบัตร" : id === "topic" ? "หัวข้อ / กิจกรรม" : "2026-08-13",
        });
        const qr = sourcePositions.qr_code || sourcePositions.qr || { x: 0.85, y: 0.82 };
        return { version: 2, elements: [
            text("recipient_name", "ชื่อผู้รับ", 52, 0.62, 0.1),
            text("topic", "หัวข้อ / กิจกรรม", 42, 0.68, 0.1),
            text("issue_date", "วันที่ออก", 30, 0.38, 0.08),
            { id: "verification_qr", source: "verification_url", kind: "qr", label: "คิวอาร์โค้ด", required: true, visible: true, box: { x: Math.max(0, qr.x - 0.07), y: Math.max(0, qr.y - 0.09), width: 0.14, height: 0.18 }, sample_value: `${location.origin}/?code=PREVIEW12345678` },
        ] };
    }

    function templateElementById(id) {
        return state.templateLayout.elements.find((element) => element.id === id);
    }

    function updateTemplateElement(id, updater, options) {
        state.templateLayout = { ...state.templateLayout, elements: state.templateLayout.elements.map((element) => element.id === id ? updater(JSON.parse(JSON.stringify(element))) : element) };
        if (!options || options.render !== false) renderTemplateEditor();
        renderTemplateProperties();
        if (!options || options.preview !== false) scheduleTemplatePreview();
    }

    function fontReferenceValue(reference) {
        return reference.source === "uploaded" ? `uploaded:${reference.id}` : `builtin:${reference.key}`;
    }

    function fontReferenceFromValue(value) {
        const [source, identifier] = String(value).split(":", 2);
        return source === "uploaded" ? { source, id: Number(identifier) } : { source: "builtin", key: identifier || "sarabun" };
    }

    async function fetchTemplateFonts() {
        state.templateFonts = await window.api.request("/fonts");
        renderFontLibrary();
    }

    function renderFontLibrary() {
        const select = $("#property-font");
        const selected = select.value;
        clear(select);
        state.templateFonts.filter((font) => font.status === "active").forEach((font) => select.appendChild(new Option(`${font.source === "builtin" ? "ฟอนต์ระบบ" : "อัปโหลด"} · ${font.display_name}`, font.source === "builtin" ? `builtin:${font.key}` : `uploaded:${font.id}`)));
        select.value = selected;
        
        const deleteSelect = $("#font-delete-select");
        if (deleteSelect) {
            const deleteSelected = deleteSelect.value;
            clear(deleteSelect);
            deleteSelect.appendChild(new Option("-- เลือกฟอนต์เพื่อลบ --", ""));
            state.templateFonts.filter((font) => font.source === "uploaded" && font.status === "active").forEach((font) => {
                deleteSelect.appendChild(new Option(font.display_name, font.id));
            });
            deleteSelect.value = deleteSelected;
        }
    }

    function renderTemplateEditor() {
        showTemplateAlignmentGuides();
        const layer = $("#template-element-layer");
        const focusedElementId = layer.contains(document.activeElement) ? document.activeElement.dataset.elementId : null;
        clear(layer);
        state.templateLayout.elements.filter((element) => element.visible !== false).forEach((element) => {
            const box = node("div", `template-element-box${element.id === state.templateSelectedElementId ? " is-selected" : ""}${element.id === state.templatePreviewErrorId ? " has-error" : ""}${state.templateDrag && element.id === state.templateDrag.element.id ? " is-dragging" : ""}`);
            box.dataset.elementId = element.id;
            box.tabIndex = 0;
            box.setAttribute("role", "button");
            box.setAttribute("aria-label", `ช่อง ${element.label} ลากเพื่อย้ายตำแหน่ง`);
            Object.assign(box.style, { left: `${element.box.x * 100}%`, top: `${element.box.y * 100}%`, width: `${element.box.width * 100}%`, height: `${element.box.height * 100}%` });
            box.appendChild(node("span", "template-element-label", element.kind === "qr" ? "QR" : element.label));
            const handle = node("span", "template-resize-handle", "");
            handle.dataset.resizeHandle = "true";
            handle.setAttribute("aria-hidden", "true");
            box.appendChild(handle);
            layer.appendChild(box);
        });
        const list = $("#template-element-list");
        clear(list);
        state.templateLayout.elements.forEach((element) => {
            const button = node("button", `template-element-list-item${element.id === state.templateSelectedElementId ? " is-selected" : ""}${element.id === state.templatePreviewErrorId ? " has-error" : ""}`, element.label);
            button.type = "button";
            button.addEventListener("click", () => { state.templateSelectedElementId = element.id; renderTemplateEditor(); renderTemplateProperties(); });
            list.appendChild(button);
        });
        applyTemplateZoom();
        const focusedBox = $$('[data-element-id]', layer).find((box) => box.dataset.elementId === focusedElementId);
        if (focusedBox) focusedBox.focus({ preventScroll: true });
    }

    function renderTemplateProperties() {
        const element = templateElementById(state.templateSelectedElementId);
        $$('[data-template-center]').forEach((button) => { button.disabled = !element || element.visible === false; });
        $("#template-properties").disabled = !element;
        if (!element) return;
        $("#property-label").value = element.label;
        $("#property-required").checked = Boolean(element.required);
        $("#property-required").disabled = element.kind === "signature" || element.source === "verification_code";
        $("#property-width").value = String(element.box.width * 100);
        $("#property-height").value = String(element.box.height * 100);
        const textOptions = element.kind !== "qr";
        $("#property-text-options").hidden = !textOptions;
        if (textOptions) {
            $("#property-font").value = fontReferenceValue(element.style.font);
            $("#property-font-size").value = element.style.font_size_px;
            $("#property-color").value = element.style.color;
            $("#property-weight").value = element.style.weight;
            $("#property-align").value = element.style.align;
            $("#property-align").disabled = element.kind === "signature";
            $("#property-line-height").value = element.style.line_height;
        }
        const formatted = ["date", "time", "date_range", "time_range"].includes(element.kind);
        $("#property-format-options").hidden = !formatted;
        if (formatted) {
            $("#property-calendar").value = element.format.calendar;
            $("#property-pattern").value = element.format.pattern;
            $("#property-prefix").value = element.format.prefix;
            $("#property-separator").value = element.format.separator;
            $("#property-suffix").value = element.format.suffix;
        }
    }

    function applyTemplateZoom() {
        const canvas = $("#template-canvas");
        const dimensions = state.templateEditingDimensions || { width: 1600, height: 1131 };
        if (state.templateZoom === "100%") {
            canvas.style.width = `${dimensions.width}px`;
            canvas.style.aspectRatio = `${dimensions.width} / ${dimensions.height}`;
        } else {
            const availableHeight = Math.max(260, window.innerHeight - canvas.parentElement.getBoundingClientRect().top - 28);
            const fittedWidth = Math.min(canvas.parentElement.clientWidth, availableHeight * dimensions.width / dimensions.height);
            canvas.style.width = isEditorPage ? `${fittedWidth}px` : "100%";
            canvas.style.aspectRatio = `${dimensions.width} / ${dimensions.height}`;
        }
    }

    function showTemplateAlignmentGuides(guides = {}) {
        for (const axis of ["x", "y"]) {
            const guide = $(`#template-guide-${axis}`);
            guide.hidden = guides[axis] === undefined || guides[axis] === null;
            if (!guide.hidden) guide.style[axis === "x" ? "left" : "top"] = `${guides[axis] * 100}%`;
        }
    }

    function centerSelectedTemplateElement(direction) {
        const element = templateElementById(state.templateSelectedElementId);
        if (!element || element.visible === false) return;
        updateTemplateElement(element.id, (current) => window.TemplateLayout.centerElement(current, direction));
        showTemplateAlignmentGuides({
            x: direction === "vertical" ? null : 0.5,
            y: direction === "horizontal" ? null : 0.5,
        });
    }

    function startTemplateMarkerDrag(event) {
        const box = event.target.closest("[data-element-id]");
        if (!box || !$("#template-preview-image").src || state.templateDrag || event.button !== 0) return;
        event.preventDefault();
        state.templateSelectedElementId = box.dataset.elementId;
        box.focus({ preventScroll: true });
        state.templateDrag = { pointerId: event.pointerId, element: JSON.parse(JSON.stringify(templateElementById(box.dataset.elementId))), startX: event.clientX, startY: event.clientY, resize: Boolean(event.target.closest("[data-resize-handle]")) };
        $("#template-element-layer").setPointerCapture(event.pointerId);
        renderTemplateEditor();
        renderTemplateProperties();
    }

    function dragTemplateMarker(event) {
        const drag = state.templateDrag;
        if (!drag || event.pointerId !== drag.pointerId) return;
        const rect = $("#template-canvas").getBoundingClientRect();
        const dx = (event.clientX - drag.startX) / rect.width;
        const dy = (event.clientY - drag.startY) / rect.height;
        const result = drag.resize
            ? { element: window.TemplateLayout.resizeElement(drag.element, { width: dx, height: dy }), guides: {} }
            : window.TemplateLayout.snapElementToCenters(
                window.TemplateLayout.nudgeElement(drag.element, dx, dy),
                state.templateLayout.elements, rect,
                { disabled: event.altKey || !$("#template-snap-centers").checked },
            );
        updateTemplateElement(drag.element.id, () => result.element, { preview: false, render: false });
        const marker = $$('[data-element-id]', $("#template-element-layer")).find((item) => item.dataset.elementId === drag.element.id);
        if (marker) {
            const box = result.element.box;
            Object.assign(marker.style, { left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.width * 100}%`, height: `${box.height * 100}%` });
        }
        showTemplateAlignmentGuides(result.guides);
    }

    function stopTemplateMarkerDrag(event) {
        if (!state.templateDrag || event.pointerId !== state.templateDrag.pointerId) return;
        state.templateDrag = null;
        const layer = $("#template-element-layer");
        if (layer.hasPointerCapture(event.pointerId)) layer.releasePointerCapture(event.pointerId);
        renderTemplateEditor();
        scheduleTemplatePreview();
    }

    function moveTemplateMarkerWithKeyboard(event) {
        const movement = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
        const box = event.target.closest("[data-element-id]");
        if (!movement || !box) return;
        event.preventDefault();
        const step = event.shiftKey ? 0.05 : 0.01;
        state.templateSelectedElementId = box.dataset.elementId;
        updateTemplateElement(box.dataset.elementId, (element) => window.TemplateLayout.nudgeElement(element, movement[0] * step, movement[1] * step));
    }

    async function openTemplateDialog(template) {
        if (!isEditorPage) {
            location.assign(template ? `/template-editor?id=${encodeURIComponent(template.id)}` : "/template-editor");
            return;
        }
        const editing = Boolean(template);
        $("#template-dialog-title").textContent = editing ? "แก้ไขแม่แบบ" : "เพิ่มแม่แบบ";
        $("#template-id").value = editing ? template.id : "";
        $("#template-name").value = editing ? template.name : "";
        $("#template-file").value = "";
        $("#template-file").required = !editing;
        $("#template-file-field").hidden = editing;
        const image = $("#template-preview-image");
        image.removeAttribute("src");
        $("#template-canvas").classList.toggle("has-image", editing);
        $("#template-empty-preview").hidden = editing;
        state.templateLayout = editing && template.layout && template.layout.version === 2 ? JSON.parse(JSON.stringify(template.layout)) : defaultTemplateLayout(editing ? template.positions : null);
        window.TemplateLayout.ensureVerificationIds(state.templateLayout, template && template.dimensions || { width: 1600, height: 1131 });
        state.templateSelectedElementId = state.templateLayout.elements[0] ? state.templateLayout.elements[0].id : null;
        state.templateEditingDimensions = editing ? template.dimensions : { width: 1600, height: 1131 };
        state.templateZoom = "fit";
        state.templatePreviewErrorId = null;
        state.templateDrag = null;
        renderTemplateEditor();
        renderTemplateProperties();
        fetchTemplateFonts().then(() => renderTemplateProperties()).catch(apiFailure);
        if (!isEditorPage) $("#template-dialog").showModal();
        $("#template-name").focus();
        editorBaseline = editorSnapshot();
        if (editing) {
            try {
                state.templateObjectUrl = await loadTemplateImage(image, template, true);
                scheduleTemplatePreview();
            } catch (error) {
                apiFailure(error);
            }
        }
    }

    async function saveTemplate(event) {
        event.preventDefault();
        const form = event.currentTarget;
        if (!form.reportValidity()) {
            return;
        }
        const id = $("#template-id").value;
        const button = $("#template-save-button");
        button.disabled = true;
        try {
            if (id) {
                await window.api.request(`/templates/${id}`, { method: "PATCH", body: { name: $("#template-name").value.trim(), layout: state.templateLayout } });
            } else {
                const body = new FormData();
                body.set("name", $("#template-name").value.trim());
                body.set("layout", window.TemplateLayout.serialize(state.templateLayout));
                body.set("file", $("#template-file").files[0]);
                await window.api.request("/templates", { method: "POST", body });
            }
            if (isEditorPage) {
                editorSaved = true;
                location.assign("/dashboard?view=templates&saved=1");
                return;
            }
            $("#template-dialog").close();
            toast(id ? "บันทึกแม่แบบแล้ว" : "สร้างแม่แบบแล้ว", "success");
            state.templates = [];
            await loadTemplates();
        } catch (error) {
            apiFailure(error);
        } finally {
            button.disabled = false;
        }
    }

    function templatePreviewValues() {
        const values = Object.fromEntries(state.templateLayout.elements
            .filter((element) => element.source !== "signature" && element.kind !== "signature")
            .map((element) => [element.source, element.sample_value !== undefined ? element.sample_value : element.kind === "qr" ? `${location.origin}/?code=PREVIEW12345678` : element.label]));
        values.verification_code = "PREVIEW12345678";
        return values;
    }

    function scheduleTemplatePreview() {
        clearTimeout(state.templatePreviewTimer);
        state.templatePreviewTimer = setTimeout(renderRealTemplatePreview, 240);
    }

    async function renderRealTemplatePreview() {
        const id = $("#template-id").value;
        const file = $("#template-file").files[0];
        if (!id && !file) return;
        if (state.templatePreviewController) state.templatePreviewController.abort();
        state.templatePreviewController = new AbortController();
        const body = new FormData();
        body.set("layout", window.TemplateLayout.serialize(state.templateLayout));
        body.set("sample_values", JSON.stringify(templatePreviewValues()));
        if (id) body.set("template_id", id); else body.set("file", file);
        $("#template-preview-status").textContent = "กำลังสร้างภาพตัวอย่าง…";
        try {
            const response = await fetch("/templates/preview", { method: "POST", headers: { Authorization: `Bearer ${window.api.token()}` }, body, signal: state.templatePreviewController.signal });
            if (!response.ok) {
                const error = await response.json();
                throw Object.assign(new Error(error.error && error.error.message), { code: error.error && error.error.code, elementId: error.error && error.error.element_id });
            }
            const blob = await response.blob();
            const preview = $("#template-real-preview");
            const oldUrl = preview.dataset.objectUrl;
            const url = URL.createObjectURL(blob);
            preview.src = url;
            preview.dataset.objectUrl = url;
            if (oldUrl) URL.revokeObjectURL(oldUrl);
            state.templatePreviewErrorId = null;
            renderTemplateEditor();
            $("#template-preview-status").textContent = "ภาพนี้ใช้ตัวเรนเดอร์เดียวกับไฟล์จริง";
        } catch (error) {
            if (error.name === "AbortError") return;
            state.templatePreviewErrorId = error.elementId || null;
            if (state.templatePreviewErrorId) {
                state.templateSelectedElementId = state.templatePreviewErrorId;
                renderTemplateEditor();
                renderTemplateProperties();
            }
            $("#template-preview-status").textContent = `${error.code || "TEMPLATE_PREVIEW_FAILED"}: ตรวจขนาดกรอบ ฟอนต์ และค่าตัวอย่าง`;
        }
    }

    function addTemplateElement() {
        const requestedKind = $("#template-new-kind").value;
        const kind = requestedKind === "verification_code" ? "text" : requestedKind;
        const signatureCount = state.templateLayout.elements.filter((item) => item.kind === "signature").length;
        const label = $("#template-new-label").value.trim() || (kind === "qr" ? "คิวอาร์โค้ด" : kind === "signature" ? `ลายเซ็นผู้ลงนาม ${signatureCount + 1}` : "ช่องข้อมูลเพิ่มเติม");
        if (kind === "signature") {
            const element = window.TemplateLayout.createElement({
                source: "signature",
                id: window.TemplateLayout.uniqueElementId(state.templateLayout.elements, "signature"),
                kind: "signature",
                label,
                required: true,
                box: { x: 0.35 + (signatureCount % 4) * 0.025, y: 0.66 - (signatureCount % 4) * 0.025, width: 0.30, height: 0.24 },
                style: templateTextStyle(28),
            });
            state.templateLayout.elements.push(element);
            state.templateSelectedElementId = element.id;
            $("#template-new-label").value = "";
            renderTemplateEditor();
            renderTemplateProperties();
            scheduleTemplatePreview();
            return;
        }
        const baseKey = window.TemplateLayout.stableId(label || `field_${state.templateLayout.elements.length + 1}`);
        let key = baseKey;
        let counter = 2;
        while (state.templateLayout.elements.some((element) => element.id === key || element.source === `custom.${key}`)) key = `${baseKey}_${counter++}`;
        const sampleValue = kind === "qr"
            ? `${location.origin}/?code=PREVIEW12345678`
            : kind === "date"
                ? "2026-08-13"
                : kind === "time"
                    ? "09:00"
                    : kind === "date_range"
                        ? { start: "2026-08-13", end: "2026-08-15" }
                        : kind === "time_range"
                            ? { start: "09:00", end: "16:30" }
                            : label;
        const id = kind === "qr"
            ? window.TemplateLayout.uniqueElementId(state.templateLayout.elements, "verification_qr")
            : key;
        const element = window.TemplateLayout.createElement({ source: requestedKind === "verification_code" ? "verification_code" : kind === "qr" ? "verification_url" : `custom.${key}`, id, kind, label: requestedKind === "verification_code" ? "ID เกียรติบัตร" : label, sample_value: sampleValue });
        if (requestedKind === "verification_code") delete element.sample_value;
        state.templateLayout.elements.push(element);
        if (kind === "qr") window.TemplateLayout.addVerificationId(state.templateLayout, element, state.templateEditingDimensions || { width: 1600, height: 1131 });
        state.templateSelectedElementId = element.id;
        $("#template-new-label").value = "";
        renderTemplateEditor();
        renderTemplateProperties();
        scheduleTemplatePreview();
    }

    function removeTemplateElement() {
        if (!state.templateSelectedElementId) return;
        state.templateLayout.elements = state.templateLayout.elements.filter((element) => element.id !== state.templateSelectedElementId);
        state.templateSelectedElementId = state.templateLayout.elements[0] ? state.templateLayout.elements[0].id : null;
        renderTemplateEditor();
        renderTemplateProperties();
        scheduleTemplatePreview();
    }

    function updateSelectedProperty(event) {
        const id = state.templateSelectedElementId;
        if (!id) return;
        updateTemplateElement(id, (element) => {
            const target = event.currentTarget;
            if (target.id === "property-label") element.label = target.value;
            if (target.id === "property-required" && element.kind !== "signature") element.required = target.checked;
            if (target.id === "property-font") element.style.font = fontReferenceFromValue(target.value);
            if (target.id === "property-font-size") element.style.font_size_px = Number(target.value);
            if (target.id === "property-color") element.style.color = target.value;
            if (target.id === "property-weight") element.style.weight = Number(target.value);
            if (target.id === "property-align" && element.kind !== "signature") element.style.align = target.value;
            if (target.id === "property-line-height") element.style.line_height = Number(target.value);
            if (target.id === "property-width") element.box.width = Math.min(1 - element.box.x, Math.max(0.00001, Number(target.value) / 100));
            if (target.id === "property-height") element.box.height = Math.min(1 - element.box.y, Math.max(0.00001, Number(target.value) / 100));
            if (target.id === "property-calendar") element.format.calendar = target.value;
            if (target.id === "property-pattern") element.format.pattern = target.value;
            if (target.id === "property-prefix") element.format.prefix = target.value;
            if (target.id === "property-separator") element.format.separator = target.value;
            if (target.id === "property-suffix") element.format.suffix = target.value;
            if (element.kind === "signature") {
                element.required = true;
                element.style.align = "center";
            }
            return element;
        });
    }

    async function uploadFont() {
        const name = $("#font-upload-name").value.trim();
        const file = $("#font-upload-file").files[0];
        if (!name || !file) {
            toast("กรุณาระบุชื่อและเลือกไฟล์ฟอนต์", "error");
            return;
        }
        
        const uploadedCount = state.templateFonts.filter(f => f.source === "uploaded" && f.status === "active").length;
        if (uploadedCount >= 3) {
            toast("อัปโหลดได้สูงสุด 3 ฟอนต์ หากต้องการอัปโหลดใหม่ กรุณาลบฟอนต์เดิมออกก่อน", "error");
            return;
        }
        const body = new FormData();
        body.set("display_name", name);
        body.set("file", file);
        try {
            await window.api.request("/fonts", { method: "POST", body });
            $("#font-upload-name").value = "";
            $("#font-upload-file").value = "";
            await fetchTemplateFonts();
            toast("เพิ่มฟอนต์ไทยแล้ว", "success");
        } catch (error) { apiFailure(error); }
    }

    async function deactivateFont(font) {
        try {
            await window.api.request(`/fonts/${font.id}`, { method: "DELETE" });
            await fetchTemplateFonts();
            toast("ถอนฟอนต์แล้ว", "success");
        } catch (error) {
            if (error.code === "FONT_IN_USE") toast("ฟอนต์นี้กำลังถูกใช้ในแม่แบบ จึงยังถอนไม่ได้", "error");
            apiFailure(error);
        }
    }

    async function deleteSelectedFont() {
        const select = $("#font-delete-select");
        if (!select || !select.value) {
            toast("กรุณาเลือกฟอนต์ที่ต้องการลบ", "error");
            return;
        }
        const fontId = Number(select.value);
        const font = state.templateFonts.find(f => f.id === fontId);
        if (font) {
            await deactivateFont(font);
        }
    }

    async function deactivateTemplate(template) {
        const answer = await requestConfirmation({ title: "ปิดการใช้งานแม่แบบ", message: `แม่แบบ “${template.name}” จะไม่สามารถใช้กับรายการใหม่ได้`, actionLabel: "ปิดใช้งาน", danger: true });
        if (!answer.confirmed) {
            return;
        }
        try {
            await window.api.request(`/templates/${template.id}/deactivate`, { method: "POST" });
            toast("ปิดการใช้งานแม่แบบแล้ว", "success");
            state.templates = [];
            await loadTemplates();
        } catch (error) {
            apiFailure(error);
        }
    }

    async function deleteTemplate(template) {
        const answer = await requestConfirmation({ title: "ลบแม่แบบ", message: `ลบแม่แบบ “${template.name}” ออกจากรายการ? เกียรติบัตรที่ออกแล้วจะยังดาวน์โหลดและตรวจสอบได้`, actionLabel: "ลบแม่แบบ", danger: true });
        if (!answer.confirmed) return;
        try {
            await window.api.request(`/templates/${template.id}`, { method: "DELETE" });
            state.templates = [];
            await loadTemplates();
            toast("ลบแม่แบบแล้ว", "success");
        } catch (error) {
            apiFailure(error);
        }
    }

    async function copyTemplate(template) {
        try {
            const copied = await window.api.request(`/templates/${template.id}/copy`, { method: "POST" });
            state.templates = [];
            await loadTemplates();
            await openTemplateDialog(copied);
            toast("สร้างสำเนาแล้ว ปรับแต่งได้โดยต้นฉบับยังคงอยู่", "success");
        } catch (error) {
            apiFailure(error);
        }
    }

    function populateTemplateSelects() {
        const active = state.templates.filter((template) => template.status === "active");
        [$("#single-template"), $("#bulk-template")].forEach((select) => {
            const value = select.value;
            const placeholder = select.firstElementChild;
            clear(select);
            select.appendChild(placeholder || new Option("เลือกแม่แบบ", ""));
            active.forEach((template) => select.appendChild(new Option(template.name, template.id)));
            select.value = value;
        });
    }

    function templateById(value) {
        return state.templates.find((template) => template.id === Number(value));
    }

    function templateNeedsSignature(template) {
        return Boolean(template && window.TemplateLayout.hasSignature(template.layout));
    }

    function updateIssuanceSignatureEditors() {
        if (!state.signatureEditors.single || !state.signatureEditors.bulk) return;
        for (const flow of ["single", "bulk"]) {
            const template = templateById($(`#${flow}-template`).value);
            state.signatureEditors[flow].setLayout(template && template.layout, template && template.id);
        }
    }

    async function renderCertificatePreview(container, template, data) {
        clear(container);
        const previousBackground = container.dataset.privateObjectUrl;
        if (previousBackground) URL.revokeObjectURL(previousBackground);
        container.dataset.privateObjectUrl = "";
        container.style.backgroundImage = "none";
        if (!template) {
            container.appendChild(node("span", "preview-label", "เลือกแม่แบบเพื่อดูตัวอย่าง"));
            return;
        }
        const backgroundBlob = await window.api.request(assetUrl(template), { responseType: "blob" });
        const backgroundUrl = URL.createObjectURL(backgroundBlob);
        container.dataset.privateObjectUrl = backgroundUrl;
        container.style.backgroundImage = `url("${backgroundUrl}")`;
        const positions = Object.assign(defaultPositions(), template.positions || {});
        const values = {
            recipient_name: data.recipient_name || "ชื่อผู้รับเกียรติบัตร",
            topic: data.topic || "หัวข้อกิจกรรม",
            issue_date: data.issue_date ? formatDate(data.issue_date) : "วันที่ออก",
            qr_code: "QR",
        };
        Object.entries(values).forEach(([key, text]) => {
            const item = node("span", `preview-field${key === "qr_code" ? " preview-field-qr" : ""}`, text);
            const position = positions[key];
            item.style.left = `${Number(position.x) * 100}%`;
            item.style.top = `${Number(position.y) * 100}%`;
            container.appendChild(item);
        });
    }

    async function prepareSingleIssuance() {
        await fetchTemplates(false);
        populateTemplateSelects();
        if (!$("#single-date").value) {
            $("#single-date").value = new Date().toISOString().slice(0, 10);
        }
        renderSingleTemplateFields();
        updateIssuanceSignatureEditors();

        wireRecipientTypeToggle("single");
    }



    function wireRecipientTypeToggle(prefix) {
        const radios = document.querySelectorAll(`input[name="${prefix}-recipient-type"]`);
        const extField = $(`#${prefix}-external-org-field`);
        const extInput = $(`#${prefix}-external-org`);
        if (!radios.length || !extField) return;
        radios.forEach((radio) => radio.addEventListener("change", () => {
            const isExternal = radio.value === "EXTERNAL" && radio.checked;
            extField.hidden = !isExternal;
            if (extInput) extInput.required = isExternal;
        }));
    }

    function renderSingleTemplateFields() {
        const host = $("#single-custom-fields");
        clear(host);
        const template = templateById($("#single-template").value);
        const layout = template && template.layout && template.layout.version === 2 ? template.layout : { version: 2, elements: [] };
        window.TemplateLayout.fieldInputs(layout).forEach((field) => {
            const shell = node("div", "field");
            const label = node("label", "", field.label);
            const inputId = `single-custom-${window.TemplateLayout.stableId(field.id || field.key)}`;
            if (["date_range", "time_range"].includes(field.kind)) {
                const grid = node("div", "form-grid compact-grid");
                ["start", "end"].forEach((endpoint) => {
                    const endpointShell = node("div", "field");
                    const input = node("input");
                    input.id = `${inputId}-${endpoint}`;
                    input.type = field.kind === "date_range" ? "date" : "time";
                    input.dataset.customKey = `${field.key}.${endpoint}`;
                    input.required = field.required;
                    input.setAttribute("aria-describedby", `${inputId}-error`);
                    const endpointLabel = node("label", "field-help", field.kind === "date_range"
                        ? (endpoint === "start" ? "วันเริ่ม" : "วันสิ้นสุด")
                        : (endpoint === "start" ? "เวลาเริ่ม" : "เวลาสิ้นสุด"));
                    endpointLabel.htmlFor = input.id;
                    endpointShell.appendChild(endpointLabel);
                    endpointShell.appendChild(input);
                    grid.appendChild(endpointShell);
                });
                shell.appendChild(label);
                shell.appendChild(grid);
                const error = node("p", "field-help");
                error.id = `${inputId}-error`;
                error.dataset.rangeError = field.key;
                error.hidden = true;
                shell.appendChild(error);
            } else {
                const input = field.kind === "multiline" ? node("textarea") : node("input");
                input.id = inputId;
                if (field.kind !== "multiline") input.type = field.kind === "date" ? "date" : field.kind === "time" ? "time" : "text";
                if (["text", "multiline"].includes(field.kind)) input.maxLength = 2000;
                input.dataset.customKey = field.key;
                input.required = field.required;
                label.htmlFor = input.id;
                shell.appendChild(label);
                shell.appendChild(input);
            }
            host.appendChild(shell);
        });
        $$('[data-custom-key]', host).forEach((input) => {
            ["input", "change"].forEach((eventName) => input.addEventListener(eventName, () => {
                resetSingleIssuanceAttempt();
                validateSingleTemplateRanges();
            }));
        });
    }

    function validateSingleTemplateRanges() {
        const host = $("#single-custom-fields");
        const template = templateById($("#single-template").value);
        const controls = new Map($$("[data-custom-key]", host).map(input => [input.dataset.customKey, input]));
        window.TemplateLayout.fieldInputs(template && template.layout).forEach((field) => {
            if (!["date_range", "time_range"].includes(field.kind)) return;
            const start = controls.get(`${field.key}.start`);
            const end = controls.get(`${field.key}.end`);
            if (!start || !end) return;
            start.setCustomValidity("");
            end.setCustomValidity("");
            start.required = field.required || Boolean(end.value);
            end.required = field.required || Boolean(start.value);
            let message = "";
            let invalidInput = null;
            if ((start.value && !end.value) || (!start.value && end.value)) {
                message = `ช่อง “${field.label}”: กรุณากรอก${field.kind === "date_range" ? "วัน" : "เวลา"}เริ่มและสิ้นสุดให้ครบทั้งสองช่อง`;
                invalidInput = start.value ? end : start;
            } else if (start.value && end.value && start.valueAsNumber > end.valueAsNumber) {
                message = `ช่อง “${field.label}”: ${field.kind === "date_range" ? "วันสิ้นสุดต้องไม่อยู่ก่อนวันเริ่ม กรุณาตรวจวันที่และปี" : "เวลาสิ้นสุดต้องไม่อยู่ก่อนเวลาเริ่ม"}`;
                invalidInput = end;
            }
            if (invalidInput) invalidInput.setCustomValidity(message);
            const error = $$("[data-range-error]", host).find(item => item.dataset.rangeError === field.key);
            if (error) {
                error.textContent = message;
                error.hidden = !message;
            }
            [start, end].forEach(input => input.setAttribute("aria-invalid", String(!input.validity.valid)));
        });
    }

    function resetSingleIssuanceAttempt() {
        state.singleIssuanceAttempt = null;
    }

    function singleIssuancePayload() {
        const template = templateById($("#single-template").value);
        const layout = template && template.layout && template.layout.version === 2 ? template.layout : { version: 2, elements: [] };
        const values = Object.fromEntries($$("[data-custom-key]", $("#single-custom-fields")).map((input) => [input.dataset.customKey, input.value]));
        const recipientType = (document.querySelector("input[name='single-recipient-type']:checked") || {}).value || "INTERNAL";
        const payload = {
            template_id: Number($("#single-template").value),
            recipient_name: $("#single-name").value.trim(),
            topic: $("#single-topic").value.trim(),
            issue_date: $("#single-date").value,
            custom_fields: window.TemplateLayout.customFieldsFromValues(values, layout),
            recipient_type: recipientType,
        };

        if (recipientType === "EXTERNAL") {
            payload.external_organization = ($("#single-external-org") && $("#single-external-org").value || "").trim();
        }
        if (templateNeedsSignature(template)) {
            payload.signatures = state.signatureEditors.single.submission();
        }
        return payload;
    }

    function singleIssuanceAttempt() {
        const payload = singleIssuancePayload();
        const fingerprint = JSON.stringify(payload);
        if (!state.singleIssuanceAttempt || state.singleIssuanceAttempt.fingerprint !== fingerprint) {
            state.singleIssuanceAttempt = { fingerprint, key: crypto.randomUUID() };
        }
        return { attempt: state.singleIssuanceAttempt, payload };
    }

    async function issueSingleCertificate(event) {
        event.preventDefault();
        const form = event.currentTarget;
        validateSingleTemplateRanges();
        if (!form.reportValidity()) {
            return;
        }
        const answer = await requestConfirmation({ title: "ยืนยันการออกเกียรติบัตร", message: `ออกเกียรติบัตรให้ “${$("#single-name").value.trim()}” ข้อมูลนี้จะแก้ไขไม่ได้หลังยืนยัน`, actionLabel: "ออกเกียรติบัตร", danger: false });
        if (!answer.confirmed) {
            return;
        }
        const button = $("#single-submit");
        button.disabled = true;
        try {
            const { attempt, payload } = singleIssuanceAttempt();
            const certificate = await window.api.request("/certificates", {
                method: "POST",
                headers: { "Idempotency-Key": attempt.key },
                body: payload,
            });
            const result = $("#single-result");
            clear(result);
            result.appendChild(node("strong", "", "ออกเกียรติบัตรสำเร็จ"));
            result.appendChild(node("p", "monospace", certificate.verification_code));
            const actions = node("div", "action-group");
            actions.appendChild(actionButton("ดาวน์โหลด", () => window.api.download(`/certificates/${encodeURIComponent(certificate.verification_code)}/download`, `certificate-${certificate.verification_code}.png`).catch(apiFailure)));
            const publicLink = node("a", "action-button", "เปิดหน้าตรวจสอบ");
            publicLink.href = `/?code=${encodeURIComponent(certificate.verification_code)}`;
            publicLink.target = "_blank";
            publicLink.rel = "noopener";
            actions.appendChild(publicLink);
            result.appendChild(actions);
            result.hidden = false;
            form.reset();
            state.signatureEditors.single.reset();
            $("#single-date").value = new Date().toISOString().slice(0, 10);
            state.certificates = [];
            renderSingleTemplateFields();
            toast("ออกเกียรติบัตรแล้ว", "success");
        } catch (error) {
            apiFailure(error);
        } finally {
            button.disabled = false;
        }
    }

    function renderBulkTemplateFields() {
        const host = $("#bulk-custom-fields");
        clear(host);
        const template = templateById($("#bulk-template").value);
        if (!template) return;
        const layout = template && template.layout && template.layout.version === 2 ? template.layout : { version: 2, elements: [] };
        window.TemplateLayout.fieldInputs(layout).forEach((field) => {
            const shell = node("div", "field");
            const label = node("label", "", field.label);
            const inputId = `bulk-custom-${window.TemplateLayout.stableId(field.id || field.key)}`;
            if (["date_range", "time_range"].includes(field.kind)) {
                const grid = node("div", "form-grid compact-grid");
                ["start", "end"].forEach((endpoint) => {
                    const endpointShell = node("div", "field");
                    const input = node("input");
                    input.id = `${inputId}-${endpoint}`;
                    input.type = field.kind === "date_range" ? "date" : "time";
                    input.dataset.bulkConstantKey = `${field.key}.${endpoint}`;
                    // Important: NOT required in bulk step 1, because it might be mapped in step 2
                    const endpointLabel = node("label", "field-help", endpoint === "start" ? "เริ่ม" : "สิ้นสุด");
                    endpointLabel.htmlFor = input.id;
                    endpointShell.appendChild(endpointLabel);
                    endpointShell.appendChild(input);
                    grid.appendChild(endpointShell);
                });
                shell.appendChild(label);
                shell.appendChild(grid);
            } else {
                const input = field.kind === "multiline" ? node("textarea") : node("input");
                input.id = inputId;
                if (field.kind !== "multiline") input.type = field.kind === "date" ? "date" : field.kind === "time" ? "time" : "text";
                if (["text", "multiline"].includes(field.kind)) input.maxLength = 2000;
                input.dataset.bulkConstantKey = field.key;
                // Important: NOT required in bulk step 1
                label.htmlFor = input.id;
                shell.appendChild(label);
                shell.appendChild(input);
            }
            host.appendChild(shell);
        });
        if (!host.childElementCount) {
            host.appendChild(node("p", "field-help", "แม่แบบนี้ไม่มีช่องรายละเอียดเพิ่มเติม"));
        }
    }

    async function prepareBulkIssuance() {
        await fetchTemplates(false);
        populateTemplateSelects();
        renderBulkTemplateFields();
        renderBulkMappingTable();
        updateIssuanceSignatureEditors();
        if (!$("#bulk-date").value) {
            $("#bulk-date").value = new Date().toISOString().slice(0, 10);
        }

        wireRecipientTypeToggle("bulk");
    }

    let currentBulkHeaders = [];
    let currentBulkRows = [];

    async function discoverHeaders() {
        currentBulkHeaders = [];
        currentBulkRows = [];
        const body = new FormData();
        body.append("source_type", "xlsx");
        const file = $("#bulk-file").files[0];
        if (file) body.append("file", file);
        
        try {
            const response = await window.api.request("/batches/headers", { method: "POST", body });
            currentBulkHeaders = response.headers || [];
            currentBulkRows = response.preview_rows || [];
        } catch (error) {
            console.error("Failed to extract headers", error);
        }

        renderBulkMappingTable();
        return currentBulkHeaders;
    }

    function renderBulkMappingTable() {
        const headers = currentBulkHeaders;
        const rows = currentBulkRows;
        const trSelectors = $("#bulk-mapping-selectors");
        const trHeaders = $("#bulk-mapping-headers");
        const tbody = $("#bulk-mapping-preview-body");
        
        clear(trSelectors);
        clear(trHeaders);
        clear(tbody);
        
        if (!headers.length) {
            append(tbody, [append(node("tr"), [node("td", "muted", "ไม่พบข้อมูลในไฟล์")])]);
            return;
        }

        const template = templateById($("#bulk-template").value);
        const layout = template && template.layout && template.layout.version === 2 ? template.layout : { version: 2, elements: [] };
        
        const fields = [
            { target: "recipient_reference", label: "รหัสอ้างอิง", required: true },
            { target: "recipient_name", label: "ชื่อผู้รับ", required: true },
            ...window.TemplateLayout.mappingFields(layout)
        ];

        // Ensure headers are unique to avoid collision bugs
        const uniqueHeaders = [];
        const seen = new Set();
        headers.forEach(h => {
            let name = h.trim();
            if (seen.has(name) || !name) name = `${name} (dup)`;
            seen.add(name);
            uniqueHeaders.push(name);
        });

        uniqueHeaders.forEach((header) => {
            const thHeader = node("th", "", header);
            trHeaders.appendChild(thHeader);
            
            const thSelector = node("th");
            const select = node("select");
            select.className = "mapping-column-select";
            select.dataset.columnName = header;
            select.appendChild(new Option("— ไม่ใช้ —", ""));
            
            fields.forEach(field => {
                const opt = new Option(field.label + (field.required ? " *" : ""), field.target);
                const shortTarget = field.target.replace(/^custom\./, "");
                const cands = [field.target, shortTarget, field.key, field.label].map(c => c && c.toLowerCase());
                if (cands.includes(header.toLowerCase())) {
                    opt.selected = true;
                }
                select.appendChild(opt);
            });
            thSelector.appendChild(select);
            trSelectors.appendChild(thSelector);
        });

        rows.forEach(row => {
            const tr = node("tr");
            uniqueHeaders.forEach(header => {
                const td = node("td", "", row[header] || "");
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
    }

    async function setBulkStep(step) {
        if (step === 2) {
            const required = [$("#bulk-template"), $("#bulk-date"), $("#bulk-topic"), $("#bulk-file")];
            if (required.some((input) => !input.checkValidity())) {
                required.find((input) => !input.checkValidity()).reportValidity();
                return;
            }
            await discoverHeaders();
        }
        state.bulkStep = step;
        $$("[data-bulk-step]").forEach((section) => { section.hidden = Number(section.dataset.bulkStep) !== step; });
        $$("[data-bulk-step-marker]").forEach((marker) => {
            const markerStep = Number(marker.dataset.bulkStepMarker);
            marker.classList.toggle("is-active", markerStep === step);
            marker.classList.toggle("is-complete", markerStep < step);
        });
    }

    function buildMapping() {
        const mapping = {};
        $$(".mapping-column-select", $("#bulk-mapping-table")).forEach(select => {
            const targetField = select.value;
            const sourceColumn = select.dataset.columnName;
            if (targetField) mapping[targetField] = sourceColumn;
        });
        return mapping;
    }

    function buildConstantFields() {
        const constants = {};
        $$("[data-bulk-constant-key]", $("#bulk-custom-fields")).forEach(input => {
            if (input.value.trim()) {
                constants[`custom.${input.dataset.bulkConstantKey}`] = input.value.trim();
            }
        });
        return constants;
    }

    async function previewBulkImport() {
        const mapping = buildMapping();
        const constantFields = buildConstantFields();
        if (!mapping.recipient_reference) {
            alert("กรุณาเลือกคอลัมน์สำหรับ 'รหัสอ้างอิง'");
            return;
        }
        if (!mapping.recipient_name) {
            alert("กรุณาเลือกคอลัมน์สำหรับ 'ชื่อผู้รับ'");
            return;
        }
        const template = templateById($("#bulk-template").value);
        const layout = template && template.layout && template.layout.version === 2 ? template.layout : { version: 2, elements: [] };
        const missingReq = window.TemplateLayout.mappingFields(layout).find(f => f.required && !mapping[f.target] && !constantFields[f.target]);
        if (missingReq) {
            alert(`กรุณาเลือกคอลัมน์หรือกรอกข้อมูลเพิ่มเติมสำหรับ '${missingReq.label}'`);
            return;
        }

        const button = $("#bulk-preview-button");
        button.disabled = true;
        try {
            const body = new FormData();
            body.set("template_id", $("#bulk-template").value);
            body.set("source_type", "xlsx");
            body.set("mapping", JSON.stringify(mapping));
            body.set("constant_fields", JSON.stringify(constantFields));
            body.set("file", $("#bulk-file").files[0]);
            const result = await window.api.request("/batches/preview", { method: "POST", body });
            hideGlobalError();
            state.bulkRows = result.rows || [];
            renderBulkRows();
            await setBulkStep(3);
        } catch (error) {
            apiFailure(error);
        } finally {
            button.disabled = false;
        }
    }

    function rowIsValid(row) {
        return Boolean(row.recipient_reference && row.recipient_reference.trim() && row.recipient_name && row.recipient_name.trim() && (!row.errors || !row.errors.length));
    }

    function refreshBulkSummary() {
        const valid = state.bulkRows.filter(rowIsValid).length;
        $("#bulk-preview-summary").textContent = `${state.bulkRows.length} แถว · พร้อมประมวลผล ${valid} · ต้องตรวจสอบ ${state.bulkRows.length - valid}`;
    }

    function validateEditedBulkRows() {
        const seen = new Set();
        const template = templateById($("#bulk-template").value);
        const layout = template && template.layout && template.layout.version === 2
            ? template.layout
            : { version: 2, elements: [] };
        const customSpecs = window.TemplateLayout.fieldInputs(layout);
        state.bulkRows.forEach((row) => {
            const errors = [];
            const reference = (row.recipient_reference || "").trim();
            const name = (row.recipient_name || "").trim();
            if (!reference) {
                errors.push({ code: "IMPORT_REQUIRED_VALUE", message: "The recipient_reference value is required.", field: "recipient_reference" });
            } else if (reference.length > MAX_RECIPIENT_VALUE_LENGTH) {
                errors.push({ code: "IMPORT_VALUE_TOO_LONG", message: "The recipient_reference value must not exceed 255 characters.", field: "recipient_reference" });
            } else if (seen.has(reference)) {
                errors.push({ code: "IMPORT_DUPLICATE_REFERENCE", message: "The recipient reference is duplicated in this import.", field: "recipient_reference" });
            } else {
                seen.add(reference);
            }
            if (!name) {
                errors.push({ code: "IMPORT_REQUIRED_VALUE", message: "The recipient_name value is required.", field: "recipient_name" });
            } else if (name.length > MAX_RECIPIENT_VALUE_LENGTH) {
                errors.push({ code: "IMPORT_VALUE_TOO_LONG", message: "The recipient_name value must not exceed 255 characters.", field: "recipient_name" });
            }
            Object.entries(row.custom_fields || {}).forEach(([field, value]) => {
                const values = value && typeof value === "object" ? Object.values(value) : [value];
                if (values.some((item) => String(item || "").trim().length > 2000)) {
                    errors.push({ code: "IMPORT_VALUE_TOO_LONG", message: `The ${field} value must not exceed 255 characters.`, field });
                }
            });
            customSpecs.filter((field) => field.required).forEach((field) => {
                const value = (row.custom_fields || {})[field.key];
                const missing = ["date_range", "time_range"].includes(field.kind)
                    ? !value || !String(value.start || "").trim() || !String(value.end || "").trim()
                    : !String(value || "").trim();
                if (missing) {
                    errors.push({ code: "IMPORT_REQUIRED_VALUE", message: `The ${field.key} value is required.`, field: field.key });
                }
            });
            row.errors = errors;
        });
    }

    function renderBulkRows() {
        validateEditedBulkRows();
        const body = $("#bulk-preview-body");
        clear(body);
        state.bulkRows.forEach((row, index) => {
            const tableRow = node("tr");
            tableRow.appendChild(append(node("td"), [node("span", "monospace", row.row_number)]));
            const referenceInput = node("input");
            referenceInput.value = row.recipient_reference || "";
            referenceInput.maxLength = 255;
            referenceInput.setAttribute("aria-label", `รหัสอ้างอิง แถว ${row.row_number}`);
            referenceInput.addEventListener("change", () => {
                state.bulkRows[index].recipient_reference = referenceInput.value;
                renderBulkRows();
            });
            tableRow.appendChild(append(node("td"), [referenceInput]));
            const nameInput = node("input");
            nameInput.value = row.recipient_name || "";
            nameInput.maxLength = 255;
            nameInput.setAttribute("aria-label", `ชื่อผู้รับ แถว ${row.row_number}`);
            nameInput.addEventListener("change", () => {
                state.bulkRows[index].recipient_name = nameInput.value;
                renderBulkRows();
            });
            tableRow.appendChild(append(node("td"), [nameInput]));
            tableRow.appendChild(renderCustomFieldInputs(row, index));
            const statusCell = node("td");
            if (rowIsValid(row)) {
                statusCell.appendChild(statusBadge("active"));
            } else {
                statusCell.appendChild(statusBadge("failed"));
                row.errors.forEach((error) => statusCell.appendChild(node("p", "audit-details", `${error.code}: ${error.message}`)));
            }
            tableRow.appendChild(statusCell);
            body.appendChild(tableRow);
        });
        $("#bulk-preview-state").hidden = state.bulkRows.length > 0;
        if (!state.bulkRows.length) {
            $("#bulk-preview-state").textContent = "ไม่มีแถวข้อมูลสำหรับตรวจสอบ";
        }
        refreshBulkSummary();
    }

    function renderCustomFieldInputs(row, index) {
        const cell = node("td", "bulk-custom-fields");
        const customFields = Object.entries(row.custom_fields || {});
        if (!customFields.length) {
            cell.textContent = "—";
            return cell;
        }
        const list = node("div", "custom-field-list");
        customFields.forEach(([field, value]) => {
            const label = node("label");
            label.appendChild(node("span", "field-help", field));
            if (value && typeof value === "object") {
                const range = node("div", "form-grid compact-grid");
                ["start", "end"].forEach((endpoint) => {
                    const input = node("input");
                    input.value = value[endpoint] || "";
                    input.maxLength = MAX_RECIPIENT_VALUE_LENGTH;
                    input.setAttribute("aria-label", `${field} ${endpoint} แถว ${row.row_number}`);
                    input.addEventListener("change", () => {
                        state.bulkRows[index].custom_fields[field][endpoint] = input.value;
                        renderBulkRows();
                    });
                    range.appendChild(input);
                });
                label.appendChild(range);
            } else {
                const input = node("input");
                input.value = value || "";
                input.maxLength = 2000;
                input.setAttribute("aria-label", `${field} แถว ${row.row_number}`);
                input.addEventListener("change", () => {
                    state.bulkRows[index].custom_fields[field] = input.value;
                    renderBulkRows();
                });
                label.appendChild(input);
            }
            list.appendChild(label);
        });
        cell.appendChild(list);
        return cell;
    }

    async function showBulkCertificatePreview() {
        const row = state.bulkRows.find(rowIsValid) || state.bulkRows[0] || {};
        try {
            await renderCertificatePreview($("#bulk-certificate-preview"), templateById($("#bulk-template").value), {
                recipient_name: row.recipient_name,
                topic: $("#bulk-topic").value,
                issue_date: $("#bulk-date").value,
            });
        } catch (error) {
            apiFailure(error);
            return;
        }
        $("#certificate-preview-dialog").showModal();
    }

    async function confirmAndProcessBulk() {
        if (!state.bulkRows.length) {
            return;
        }
        validateEditedBulkRows();
        renderBulkRows();
        const answer = await requestConfirmation({ title: "ยืนยันรายการแบบกลุ่ม", message: `สร้างรายการ ${state.bulkRows.length} แถว ระบบจะเก็บผลของแต่ละแถวแยกกัน`, actionLabel: "สร้างและประมวลผล", danger: false });
        if (!answer.confirmed) {
            return;
        }
        let template = templateById($("#bulk-template").value);
        if (!template) {
            try {
                await fetchTemplates(false);
                template = templateById($("#bulk-template").value);
            } catch (error) {
                apiFailure(error);
                return;
            }
        }
        let bulkSignature = null;
        if (templateNeedsSignature(template)) {
            try {
                bulkSignature = state.signatureEditors.bulk.submission();
            } catch (error) {
                apiFailure(error);
                return;
            }
        }
        try {
            if (!state.bulkBatch) {
                await setBulkStep(4);
                setBulkProcessing(true, "กำลังสร้างรายการ", "กำลังบันทึกข้อมูลที่ตรวจสอบแล้ว", 18);
                state.bulkBatch = await window.api.request("/batches", {
                    method: "POST",
                    body: (() => {
                        const recipientType = (document.querySelector("input[name='bulk-recipient-type']:checked") || {}).value || "INTERNAL";
                        const body = {
                            template_id: Number($("#bulk-template").value),
                            topic: $("#bulk-topic").value.trim(),
                            issue_date: $("#bulk-date").value,
                            source_type: "xlsx",
                            event_metadata: {},
                            recipient_type: recipientType,
                            rows: state.bulkRows.map((row) => ({
                                row_number: row.row_number,
                                recipient_reference: row.recipient_reference || null,
                                recipient_name: row.recipient_name || null,
                                custom_fields: row.custom_fields || {},
                                errors: row.errors || [],
                            })),
                        };

                        if (recipientType === "EXTERNAL") {
                            body.external_organization = ($("#bulk-external-org") && $("#bulk-external-org").value || "").trim();
                        }
                        return body;
                    })(),
                });
            }
            if (state.bulkBatch.status === "validated") {
                setBulkProcessing(true, "กำลังประมวลผล", "ระบบกำลังสร้างเกียรติบัตรสำหรับแถวที่ผ่านการตรวจสอบ", 55);
                const processOptions = templateNeedsSignature(template)
                    ? { method: "POST", body: { signatures: bulkSignature } }
                    : { method: "POST" };
                state.bulkBatch = await window.api.request(`/batches/${state.bulkBatch.id}/process`, processOptions);
                if (templateNeedsSignature(template)) {
                    state.signatureEditors.bulk.reset();
                }
            }
            state.batches = [];
            state.certificates = [];
            renderBulkResult(state.bulkBatch);
            await renderBulkIndividualDownloads(state.bulkBatch);
        } catch (error) {
            setBulkProcessing(false, "ไม่สามารถประมวลผลได้", "ตรวจสอบรายละเอียดทางเทคนิคแล้วลองอีกครั้ง", 0);
            if (state.bulkBatch && state.bulkBatch.status === "validated") {
                $("#bulk-result-actions").hidden = false;
                $("#bulk-retry-button").hidden = false;
                $("#bulk-retry-button").textContent = "ลองเริ่มประมวลผลอีกครั้ง";
                $("#bulk-download-button").hidden = true;
            }
            apiFailure(error);
        }
    }

    function setBulkProcessing(spinning, title, message, progress) {
        $("#bulk-processing-icon").classList.toggle("is-spinning", spinning);
        $("#bulk-processing-icon").textContent = spinning ? "↻" : "!";
        $("#bulk-processing-title").textContent = title;
        $("#bulk-processing-message").textContent = message;
        $("#bulk-progress").style.width = `${progress}%`;
        $(".progress-track").setAttribute("aria-valuenow", String(progress));
    }

    function renderBulkResult(batch) {
        setBulkProcessing(false, batch.failure_count ? "ประมวลผลเสร็จพร้อมรายการที่ต้องตรวจสอบ" : "ประมวลผลสำเร็จ", batch.failure_count ? "สามารถลองใหม่เฉพาะแถวที่ล้มเหลวได้" : "เกียรติบัตรพร้อมดาวน์โหลด", 100);
        $("#bulk-processing-icon").textContent = batch.failure_count ? "!" : "✓";
        $("[data-bulk-result='total']").textContent = batch.total_count;
        $("[data-bulk-result='success']").textContent = batch.success_count;
        $("[data-bulk-result='failure']").textContent = batch.failure_count;
        $("#bulk-result-summary").hidden = false;
        $("#bulk-result-actions").hidden = false;
        $("#bulk-retry-button").hidden = batch.failure_count === 0;
        $("#bulk-download-button").hidden = batch.success_count === 0;
    }

    async function renderBulkIndividualDownloads(batch) {
        const container = $("#bulk-individual-downloads");
        clear(container);
        container.hidden = true;
        if (!batch.success_count) {
            return;
        }
        try {
            const certificates = [];
            for (let offset = 0; ; offset += 100) {
                const result = await window.api.request(`/dashboard/certificates?batch_id=${batch.id}&offset=${offset}&limit=100`);
                certificates.push(...result.items);
                if (certificates.length >= result.total || !result.items.length) break;
            }
            const certificatesById = new Map(certificates.map((certificate) => [certificate.id, certificate]));
            const successfulRows = (batch.rows || []).filter((row) => certificatesById.has(row.certificate_id));
            if (!successfulRows.length) {
                return;
            }
            container.appendChild(node("p", "field-help", "ดาวน์โหลดเกียรติบัตรรายบุคคล"));
            const actions = node("div", "action-group");
            successfulRows.forEach((row) => {
                const certificate = certificatesById.get(row.certificate_id);
                actions.appendChild(actionButton(`ดาวน์โหลด ${certificate.recipient_name}`, () => window.api.download(`/certificates/${encodeURIComponent(certificate.verification_code)}/download`, `certificate-${certificate.verification_code}.png`).catch(apiFailure)));
            });
            container.appendChild(actions);
            container.hidden = false;
        } catch (error) {
            apiFailure(error);
        }
    }

    async function retryCurrentBulk() {
        if (!state.bulkBatch) {
            return;
        }
        const answer = await requestConfirmation({ title: "ลองประมวลผลอีกครั้ง", message: "ระบบจะลองเฉพาะแถวที่ล้มเหลว และจะไม่สร้างเกียรติบัตรที่สำเร็จแล้วซ้ำ", actionLabel: "ลองอีกครั้ง", danger: false });
        if (!answer.confirmed) {
            return;
        }
        let template = templateById($("#bulk-template").value);
        if (!template) {
            try {
                await fetchTemplates(false);
                template = templateById($("#bulk-template").value);
            } catch (error) {
                apiFailure(error);
                return;
            }
        }
        let signature = null;
        if (templateNeedsSignature(template)) {
            if (state.bulkBatch.status === "validated") {
                try {
                    signature = state.signatureEditors.bulk.submission();
                } catch (error) {
                    apiFailure(error);
                    return;
                }
            } else {
                signature = await requestTransientSignature(template, "ใส่ลายเซ็นเพื่อลองแถวที่ล้มเหลว");
                if (signature === undefined) return;
            }
        }
        try {
            setBulkProcessing(true, "กำลังลองอีกครั้ง", "กำลังประมวลผลเฉพาะแถวที่ล้มเหลว", 55);
            const path = state.bulkBatch.status === "validated" ? "process" : "retry";
            const options = templateNeedsSignature(template)
                ? { method: "POST", body: { signatures: signature } }
                : { method: "POST" };
            state.bulkBatch = await window.api.request(`/batches/${state.bulkBatch.id}/${path}`, options);
            if (templateNeedsSignature(template)) {
                if (path === "process") state.signatureEditors.bulk.reset();
                else state.signatureEditors.dialog.reset();
            }
            renderBulkResult(state.bulkBatch);
            await renderBulkIndividualDownloads(state.bulkBatch);
        } catch (error) {
            apiFailure(error);
        }
    }

    function resetBulkForm() {
        $("#bulk-form").reset();
        $("#bulk-date").value = new Date().toISOString().slice(0, 10);
        state.bulkRows = [];
        state.bulkBatch = null;
        if (state.signatureEditors.bulk) state.signatureEditors.bulk.reset();
        $("#bulk-result-summary").hidden = true;
        $("#bulk-result-actions").hidden = true;
        clear($("#bulk-individual-downloads"));
        $("#bulk-individual-downloads").hidden = true;
        setBulkStep(1);
    }

    async function loadBatches() {
        const list = $("#batch-list");
        setLoading(list);
        await Promise.all([fetchTemplates(true), fetchBatches(true)]);
        renderBatches();
    }

    function filteredBatches() {
        const search = $("#batch-search").value.trim().toLocaleLowerCase("th");
        const status = $("#batch-status").value;
        return state.batches.filter((batch) => {
            const matchesSearch = !search || String(batch.id).includes(search) || String(batch.topic).toLocaleLowerCase("th").includes(search);
            return matchesSearch && (!status || batch.status === status);
        });
    }

    function renderBatches() {
        const list = $("#batch-list");
        clear(list);
        const pageItems = state.batches;
        if (!pageItems.length) {
            list.appendChild(node("p", "empty-state", "ไม่พบรายการประมวลผล"));
        }
        pageItems.forEach((batch) => {
            const content = node("div");
            content.appendChild(node("h3", "", `#${batch.id} · ${batch.topic}`));
            content.appendChild(node("p", "", `${formatDate(batch.issue_date)} · ${batch.source_type}`));
            const counts = node("div", "batch-counts");
            counts.appendChild(node("span", "", `ทั้งหมด ${batch.total_count}`));
            counts.appendChild(node("span", "", `สำเร็จ ${batch.success_count}`));
            counts.appendChild(node("span", "", `ล้มเหลว ${batch.failure_count}`));
            counts.appendChild(statusBadge(batch.status));
            content.appendChild(counts);
            if (batch.failure_count) {
                const details = node("details");
                details.appendChild(node("summary", "text-button", `รายละเอียด ${batch.failure_count} แถวที่ล้มเหลว`));
                details.addEventListener("toggle", async () => {
                    if (!details.open || details.dataset.loaded) return;
                    details.dataset.loaded = "loading";
                    try {
                        const full = await window.api.request(`/batches/${batch.id}`);
                        (full.rows || []).filter(row => row.error_code).forEach(row => details.appendChild(node("p", "audit-details", `Row ${row.row_number} · ${row.error_code}: ${row.error_message}`)));
                        details.dataset.loaded = "yes";
                    } catch (error) { delete details.dataset.loaded; apiFailure(error); }
                });
                content.appendChild(details);
            }
            const actions = node("div", "action-group");
            const template = templateById(batch.template_id);
            const canProcess = template && template.status === "active";
            if (!canProcess && ["validated", "partial", "failed"].includes(batch.status)) {
                content.appendChild(node("p", "field-help", "แม่แบบถูกลบหรือปิดใช้งานแล้ว หากต้องการออกใบใหม่ กรุณาสร้างรายการด้วยแม่แบบที่ใช้งานได้"));
            }
            if (canProcess && batch.status === "validated") {
                actions.appendChild(actionButton("ประมวลผล", () => processBatchFromHistory(batch)));
            }
            if (canProcess && batch.failure_count > 0 && ["partial", "failed"].includes(batch.status)) {
                actions.appendChild(actionButton("ลองแถวที่ล้มเหลว", () => retryBatchFromHistory(batch)));
            }
            if (batch.success_count > 0) {
                actions.appendChild(actionButton("ดาวน์โหลด ZIP", () => window.api.download(`/batches/${batch.id}/download`, `batch-${batch.id}-certificates.zip`).catch(apiFailure)));
            }
            list.appendChild(append(node("article", "batch-card"), [content, actions]));
        });
        pagination($("#batches-pagination"), state.batchPage, state.batchTotal || 0, (page) => { state.batchPage = page; loadBatches().catch(apiFailure); });
    }

    function requestTransientSignature(template, title) {
        if (!templateNeedsSignature(template)) return Promise.resolve(null);
        const dialog = $("#signature-request-dialog");
        state.signatureEditors.dialog.setLayout(template.layout, template.id);
        $("#signature-request-title").textContent = title;
        dialog.showModal();
        state.signatureEditors.dialog.focus();
        return new Promise((resolve) => { state.signatureResolve = resolve; });
    }

    function cancelTransientSignature() {
        const resolve = state.signatureResolve;
        state.signatureResolve = null;
        if (state.signatureEditors.dialog) state.signatureEditors.dialog.reset();
        const dialog = $("#signature-request-dialog");
        if (dialog.open) dialog.close();
        if (resolve) resolve(undefined);
    }

    function handleTransientSignatureClose() {
        const resolve = state.signatureResolve;
        if (!resolve) return;
        state.signatureResolve = null;
        state.signatureEditors.dialog.reset();
        resolve(undefined);
    }

    function submitTransientSignature(event) {
        event.preventDefault();
        try {
            const value = state.signatureEditors.dialog.submission();
            const resolve = state.signatureResolve;
            state.signatureResolve = null;
            $("#signature-request-dialog").close();
            if (resolve) resolve(value);
        } catch (error) {
            state.signatureEditors.dialog.focus();
        }
    }

    async function processBatchFromHistory(batch) {
        const answer = await requestConfirmation({ title: "ประมวลผลรายการ", message: `เริ่มสร้างเกียรติบัตรจากรายการ #${batch.id}`, actionLabel: "ประมวลผล", danger: false });
        if (!answer.confirmed) return;
        try {
            await fetchTemplates(false);
            const template = templateById(batch.template_id);
            const signature = await requestTransientSignature(template, "ใส่ลายเซ็นเพื่อประมวลผลรายการ");
            if (signature === undefined) return;
            const options = signature ? { method: "POST", body: { signatures: signature } } : { method: "POST" };
            await window.api.request(`/batches/${batch.id}/process`, options);
            if (signature) state.signatureEditors.dialog.reset();
            toast("ประมวลผลรายการแล้ว", "success");
            state.batches = [];
            state.certificates = [];
            await loadBatches();
        } catch (error) { apiFailure(error); }
    }

    async function retryBatchFromHistory(batch) {
        const answer = await requestConfirmation({ title: "ลองแถวที่ล้มเหลว", message: `ลองประมวลผลเฉพาะแถวที่ล้มเหลวในรายการ #${batch.id}`, actionLabel: "ลองอีกครั้ง", danger: false });
        if (!answer.confirmed) return;
        try {
            await fetchTemplates(false);
            const template = templateById(batch.template_id);
            const signature = await requestTransientSignature(template, "ใส่ลายเซ็นเพื่อลองแถวที่ล้มเหลว");
            if (signature === undefined) return;
            const options = signature ? { method: "POST", body: { signatures: signature } } : { method: "POST" };
            await window.api.request(`/batches/${batch.id}/retry`, options);
            if (signature) state.signatureEditors.dialog.reset();
            toast("ลองประมวลผลแถวที่ล้มเหลวแล้ว", "success");
            state.batches = [];
            state.certificates = [];
            await loadBatches();
        } catch (error) { apiFailure(error); }
    }

    async function loadCertificates() {
        const tableBody = $("#certificates-table-body");
        clear(tableBody);
        $("#certificates-state").hidden = false;
        $("#certificates-state").textContent = "กำลังโหลดข้อมูล…";
        await fetchCertificates(true);
        renderCertificates();
    }

    function filteredCertificates() {
        const search = $("#certificate-search").value.trim().toLocaleLowerCase("th");
        const status = $("#certificate-status").value;
        return state.certificates.filter((certificate) => {
            const haystack = `${certificate.recipient_name} ${certificate.topic} ${certificate.verification_code}`.toLocaleLowerCase("th");
            return (!search || haystack.includes(search)) && (!status || certificate.status === status);
        });
    }

    function renderCertificates() {
        const tableBody = $("#certificates-table-body");
        const tableState = $("#certificates-state");
        clear(tableBody);
        const pageItems = state.certificates;
        tableState.hidden = pageItems.length > 0;
        tableState.textContent = "ไม่พบเกียรติบัตรตามเงื่อนไข";
        pageItems.forEach((certificate) => {
            const row = node("tr");
            const identity = append(node("div", "cell-primary"), [node("strong", "", certificate.recipient_name), node("small", "monospace", certificate.verification_code)]);
            const actions = node("div", "action-group");
            if (certificate.status === "active") {
                actions.appendChild(actionButton("ดาวน์โหลด", () => window.api.download(`/certificates/${encodeURIComponent(certificate.verification_code)}/download`, `certificate-${certificate.verification_code}.png`).catch(apiFailure)));
                actions.appendChild(actionButton("เพิกถอน", () => revokeCertificate(certificate), true));
            }
            if (certificate.status === "revoked") {
                actions.appendChild(actionButton("ลบถาวร", () => deleteCertificate(certificate), true));
            }
            const publicLink = node("a", "action-button", "ตรวจสอบ");
            publicLink.href = `/?code=${encodeURIComponent(certificate.verification_code)}`;
            publicLink.target = "_blank";
            publicLink.rel = "noopener";
            actions.appendChild(publicLink);
            [identity, node("span", "", certificate.topic), node("span", "", formatDate(certificate.issue_date)), statusBadge(certificate.status), actions].forEach((content) => {
                const cell = node("td");
                cell.appendChild(content);
                row.appendChild(cell);
            });
            tableBody.appendChild(row);
        });
        pagination($("#certificates-pagination"), state.certificatePage, state.certificateTotal || 0, (page) => { state.certificatePage = page; loadCertificates().catch(apiFailure); });
    }

    async function deleteCertificate(certificate) {
        const answer = await requestConfirmation({ title: "ลบเกียรติบัตรถาวร", message: `ลบเกียรติบัตรของ “${certificate.recipient_name}” และไฟล์ภาพอย่างถาวร ลิงก์และ QR เดิมจะไม่พบข้อมูล กู้คืนไม่ได้ โดยยังเก็บประวัติการดำเนินการและชุดออกใบไว้`, actionLabel: "ลบถาวร", danger: true });
        if (!answer.confirmed) return;
        try {
            await window.api.request(`/certificates/${encodeURIComponent(certificate.verification_code)}`, { method: "DELETE" });
            toast("ลบเกียรติบัตรแล้ว", "success");
            state.certificatePage = 1;
            await loadCertificates();
        } catch (error) {
            apiFailure(error);
        }
    }

    async function revokeCertificate(certificate) {
        const answer = await requestConfirmation({ title: "เพิกถอนเกียรติบัตร", message: `เกียรติบัตรของ “${certificate.recipient_name}” จะยังตรวจสอบสถานะได้ แต่ดาวน์โหลดไม่ได้`, actionLabel: "เพิกถอน", danger: true, inputLabel: "เหตุผลการเพิกถอน", inputRequired: true });
        if (!answer.confirmed) {
            return;
        }
        try {
            await window.api.request(`/certificates/${encodeURIComponent(certificate.verification_code)}/revoke`, { method: "POST", body: { reason: answer.value } });
            toast("เพิกถอนเกียรติบัตรแล้ว", "success");
            state.certificates = [];
            await loadCertificates();
        } catch (error) {
            apiFailure(error);
        }
    }

    function requestConfirmation(options) {
        const dialog = $("#confirm-dialog");
        $("#confirm-title").textContent = options.title;
        $("#confirm-message").textContent = options.message;
        $("#confirm-accept").textContent = options.actionLabel || "ยืนยัน";
        $("#confirm-accept").className = `button ${options.danger ? "button-danger" : "button-primary"}`;
        const field = $("#confirm-input-field");
        field.hidden = !options.inputLabel;
        $("#confirm-input-label").textContent = options.inputLabel || "ข้อมูลเพิ่มเติม";
        $("#confirm-input").value = "";
        $("#confirm-input").required = Boolean(options.inputRequired);
        state.confirmRequiresInput = Boolean(options.inputRequired);
        dialog.returnValue = "";
        dialog.showModal();
        return new Promise((resolve) => { state.confirmResolve = resolve; });
    }

    function resolveConfirmation() {
        if (!state.confirmResolve) {
            return;
        }
        const resolve = state.confirmResolve;
        state.confirmResolve = null;
        resolve({ confirmed: $("#confirm-dialog").returnValue === "confirm", value: $("#confirm-input").value.trim() });
    }

    function wireEvents() {
        $$(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
        $$('[data-open-view]').forEach((button) => button.addEventListener("click", () => showView(button.dataset.openView)));
        $("#dismiss-alert").addEventListener("click", hideGlobalError);
        $("#logout-button").addEventListener("click", () => { window.api.clearToken(); window.location.assign("/login"); });
        $("#open-navigation").addEventListener("click", openNavigation);
        $("#close-navigation").addEventListener("click", closeNavigation);
        $("#navigation-scrim").addEventListener("click", closeNavigation);
        window.addEventListener("resize", updateNavigationAccessibility);
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && $("#sidebar").classList.contains("is-open")) {
                closeNavigation();
            }
        });
        $$('[data-close-dialog]').forEach((button) => button.addEventListener("click", () => { if (isEditorPage && button.dataset.closeDialog === "template-dialog") leaveEditor(); else document.getElementById(button.dataset.closeDialog).close(); }));

        $("#create-organization-button").addEventListener("click", () => openOrganizationDialog(null).catch(apiFailure));
        $("#organization-form").addEventListener("submit", saveOrganization);
        $("#organization-selector").addEventListener("change", (event) => setOrganizationDialogMode(event.currentTarget.value === "new" ? "new" : "personnel"));
        $("#add-personnel-button").addEventListener("click", () => {
            const organizationId = $("#personnel-organization-id").value;
            $("#personnel-dialog").close();
            openOrganizationDialog(null, organizationId).catch(apiFailure);
        });
        $("#password-form").addEventListener("submit", resetPassword);
        $("#organization-filters").addEventListener("submit", (event) => { event.preventDefault(); state.organizationPage = 1; loadOrganizations().catch(apiFailure); });
        $("#audit-filters").addEventListener("submit", (event) => { event.preventDefault(); state.auditPage = 1; loadAuditLogs().catch(apiFailure); });

        $("#create-template-button").addEventListener("click", () => openTemplateDialog(null));
        $("#template-form").addEventListener("submit", saveTemplate);
        $("#template-element-layer").addEventListener("pointerdown", startTemplateMarkerDrag);
        $("#template-element-layer").addEventListener("pointermove", dragTemplateMarker);
        $("#template-element-layer").addEventListener("pointerup", stopTemplateMarkerDrag);
        $("#template-element-layer").addEventListener("pointercancel", stopTemplateMarkerDrag);
        $("#template-element-layer").addEventListener("lostpointercapture", stopTemplateMarkerDrag);
        $("#template-element-layer").addEventListener("keydown", moveTemplateMarkerWithKeyboard);
        $$('[data-template-center]').forEach((button) => button.addEventListener("click", () => centerSelectedTemplateElement(button.dataset.templateCenter)));
        $("#template-snap-centers").addEventListener("change", () => showTemplateAlignmentGuides());
        $("#template-add-element").addEventListener("click", addTemplateElement);
        $("#template-remove-element").addEventListener("click", removeTemplateElement);
        $("#font-upload-button").addEventListener("click", uploadFont);
        if ($("#font-delete-button")) {
            $("#font-delete-button").addEventListener("click", deleteSelectedFont);
        }
        ["property-label", "property-required", "property-font", "property-font-size", "property-color", "property-weight", "property-align", "property-line-height", "property-calendar", "property-pattern", "property-prefix", "property-separator", "property-suffix", "property-width", "property-height"].forEach((id) => document.getElementById(id).addEventListener("input", updateSelectedProperty));
        $("#template-file").addEventListener("change", () => {
            if (state.templateObjectUrl) URL.revokeObjectURL(state.templateObjectUrl);
            delete $("#template-preview-image").dataset.privateObjectUrl;
            const file = $("#template-file").files[0];
            state.templateObjectUrl = file ? URL.createObjectURL(file) : null;
            $("#template-preview-image").src = state.templateObjectUrl || "";
            $("#template-empty-preview").hidden = Boolean(file);
            $("#template-canvas").classList.toggle("has-image", Boolean(file));
            if (file) {
                const probe = new Image();
                probe.addEventListener("load", () => {
                    state.templateEditingDimensions = { width: probe.naturalWidth, height: probe.naturalHeight };
                    applyTemplateZoom();
                    scheduleTemplatePreview();
                });
                probe.src = state.templateObjectUrl;
            }
        });
        $("#template-dialog").addEventListener("close", () => {
            if (state.templateDrag) stopTemplateMarkerDrag({ pointerId: state.templateDrag.pointerId });
            showTemplateAlignmentGuides();
            if (state.templateObjectUrl) URL.revokeObjectURL(state.templateObjectUrl);
            state.templateObjectUrl = null;
            delete $("#template-preview-image").dataset.privateObjectUrl;
            if (state.templatePreviewController) state.templatePreviewController.abort();
            const preview = $("#template-real-preview");
            if (preview.dataset.objectUrl) URL.revokeObjectURL(preview.dataset.objectUrl);
            preview.removeAttribute("src");
            preview.dataset.objectUrl = "";
        });

        [$("#single-name"), $("#single-topic"), $("#single-date")].forEach((input) => input.addEventListener("input", resetSingleIssuanceAttempt));
        $("#single-template").addEventListener("change", () => {
            resetSingleIssuanceAttempt();
            if (state.signatureEditors.single) state.signatureEditors.single.reset();
            renderSingleTemplateFields();
            updateIssuanceSignatureEditors();
        });
        $("#single-form").addEventListener("submit", issueSingleCertificate);
        $("#single-form").addEventListener("reset", resetSingleIssuanceAttempt);

        $("#bulk-template").addEventListener("change", () => {
            if (state.signatureEditors.bulk) state.signatureEditors.bulk.reset();
            renderBulkTemplateFields();
            renderBulkMappingTable();
            updateIssuanceSignatureEditors();
        });
        $$('[data-bulk-next]').forEach((button) => button.addEventListener("click", () => setBulkStep(Number(button.dataset.bulkNext)).catch(apiFailure)));
        $$('[data-bulk-back]').forEach((button) => button.addEventListener("click", () => setBulkStep(Number(button.dataset.bulkBack)).catch(apiFailure)));
        $("#bulk-preview-button").addEventListener("click", previewBulkImport);
        $("#bulk-preview-certificate").addEventListener("click", showBulkCertificatePreview);
        $("#bulk-confirm-button").addEventListener("click", confirmAndProcessBulk);
        $("#bulk-retry-button").addEventListener("click", retryCurrentBulk);
        $("#bulk-download-button").addEventListener("click", () => { if (state.bulkBatch) window.api.download(`/batches/${state.bulkBatch.id}/download`, `batch-${state.bulkBatch.id}-certificates.zip`).catch(apiFailure); });
        $("#bulk-reset-button").addEventListener("click", resetBulkForm);

        [$("#batch-search"), $("#batch-status")].forEach((input) => input.addEventListener("input", () => { state.batchPage = 1; clearTimeout(state.batchSearchTimer); state.batchSearchTimer = setTimeout(() => loadBatches().catch(apiFailure), 250); }));
        [$("#certificate-search"), $("#certificate-status")].forEach((input) => input.addEventListener("input", () => { state.certificatePage = 1; clearTimeout(state.certificateSearchTimer); state.certificateSearchTimer = setTimeout(() => loadCertificates().catch(apiFailure), 250); }));

        $("#confirm-cancel").formNoValidate = true;
        $("#confirm-accept").addEventListener("click", (event) => {
            const input = $("#confirm-input");
            if (state.confirmRequiresInput && !input.value.trim()) {
                event.preventDefault();
                input.setCustomValidity("กรุณาระบุเหตุผล");
                input.reportValidity();
            } else {
                input.setCustomValidity("");
            }
        });
        $("#confirm-input").addEventListener("input", (event) => event.currentTarget.setCustomValidity(""));
        $("#confirm-dialog").addEventListener("close", resolveConfirmation);
        $("#signature-request-form").addEventListener("submit", submitTransientSignature);
        $("#signature-request-cancel").addEventListener("click", cancelTransientSignature);
        $("#signature-request-dialog").addEventListener("close", handleTransientSignatureClose);
        updateNavigationAccessibility();
    }


    async function bootstrap() {
        if (!window.api.token()) {
            window.location.replace("/login");
            return;
        }
        if (isEditorPage) setupEditorPage();
        wireEvents();
        state.signatureEditors.single = window.SignaturePad.mountGroup($("#single-signature-host"));
        state.signatureEditors.bulk = window.SignaturePad.mountGroup($("#bulk-signature-host"));
        state.signatureEditors.dialog = window.SignaturePad.mountGroup($("#dialog-signature-host"));

        try {
            state.user = await window.api.request("/me");
            $("#account-name").textContent = state.user.username;
            $("#account-avatar").textContent = state.user.username.slice(0, 1);
            const isAdmin = state.user.role === "admin";
            $("#account-role").textContent = isAdmin ? "ผู้ดูแลระบบ" : "บัญชีหน่วยงาน";
            $("#admin-navigation").hidden = !isAdmin;
            $("#organization-navigation").hidden = isAdmin;
            if (isEditorPage) {
                if (isAdmin) { location.replace("/dashboard"); return; }
                const id = new URLSearchParams(location.search).get("id");
                if (id && !/^\d+$/.test(id)) throw new Error("รหัสแม่แบบไม่ถูกต้อง");
                const template = id ? await window.api.request(`/templates/${id}`) : null;
                await openTemplateDialog(template);
            } else {
                const params = new URLSearchParams(location.search);
                await showView(isAdmin ? "admin-overview" : params.get("view") === "templates" ? "templates" : "organization-overview");
                if (params.get("saved") === "1") toast("บันทึกแม่แบบเรียบร้อยแล้ว", "success");
            }
        } catch (error) {
            apiFailure(error);
        }
    }

    bootstrap();
})();
