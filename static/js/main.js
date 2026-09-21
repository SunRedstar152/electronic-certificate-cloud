(function initializePublicVerification() {
    "use strict";

    const form = document.getElementById("verify-form");
    if (!form) {
        return;
    }

    const input = document.getElementById("certificate-code");
    const button = document.getElementById("verify-button");
    const result = document.getElementById("verification-result");
    const icon = document.getElementById("result-icon");
    const heading = document.getElementById("result-heading");
    const summary = document.getElementById("result-summary");
    const details = document.getElementById("certificate-information");
    const preview = document.getElementById("certificate-preview");
    const previewImage = document.getElementById("certificate-preview-image");
    const downloads = document.getElementById("certificate-downloads");
    const downloadPdf = document.getElementById("download-pdf");
    const downloadJpg = document.getElementById("download-jpg");
    const downloadPng = document.getElementById("download-png");
    const technicalError = document.getElementById("technical-error");
    const errorCode = document.getElementById("error-code");
    const errorMessage = document.getElementById("error-message");

    const fields = {
        recipient_name: document.getElementById("result-name"),
        topic: document.getElementById("result-topic"),
        organization: document.getElementById("result-organization"),
        issue_date: document.getElementById("result-date"),
        verification_code: document.getElementById("result-code"),
        status: document.getElementById("result-status"),
    };

    function formatDate(value) {
        if (!value) {
            return "—";
        }
        const date = new Date(`${value}T00:00:00`);
        return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("th-TH", { dateStyle: "long" }).format(date);
    }

    function setTechnicalError(error) {
        errorCode.textContent = error.code || "HTTP_ERROR";
        errorMessage.textContent = error.message || "The request could not be completed.";
        technicalError.hidden = false;
    }

    function resetResult() {
        result.hidden = false;
        details.hidden = true;
        preview.hidden = true;
        previewImage.removeAttribute("src");
        previewImage.alt = "";
        delete previewImage.dataset.fallbackSrc;
        downloads.hidden = true;
        [downloadPdf, downloadJpg, downloadPng].forEach((link) => link.removeAttribute("href"));
        details.open = false;
        technicalError.hidden = true;
        errorCode.textContent = "";
        errorMessage.textContent = "";
    }

    function showCertificate(certificate) {
        const isRevoked = certificate.status === "revoked";
        result.dataset.state = isRevoked ? "revoked" : "active";
        icon.textContent = isRevoked ? "!" : "✓";
        heading.textContent = isRevoked ? "เกียรติบัตรถูกเพิกถอน" : "เกียรติบัตรถูกต้อง";
        summary.textContent = isRevoked
            ? "พบข้อมูลในระบบ แต่เกียรติบัตรนี้ถูกเพิกถอนและไม่สามารถดาวน์โหลดได้"
            : "พบข้อมูลเกียรติบัตรที่ยังใช้งานอยู่ในระบบ";
        fields.recipient_name.textContent = certificate.recipient_name || "—";
        fields.topic.textContent = certificate.topic || "—";
        fields.organization.textContent = certificate.organization && certificate.organization.name ? certificate.organization.name : "—";
        fields.issue_date.textContent = formatDate(certificate.issue_date);
        fields.verification_code.textContent = certificate.verification_code || "—";
        fields.status.textContent = isRevoked ? "เพิกถอน" : "ใช้งาน";



        const externalOrgRow = document.getElementById("result-external-org-row");
        const externalOrgField = document.getElementById("result-external-org");
        if (certificate.external_organization) {
            externalOrgField.textContent = certificate.external_organization;
            externalOrgRow.hidden = false;
        } else {
            externalOrgRow.hidden = true;
        }

        details.hidden = false;
        details.open = isRevoked;
        if (!isRevoked) {
            const encodedCode = encodeURIComponent(certificate.verification_code);
            previewImage.alt = `ใบเกียรติบัตรของ ${certificate.recipient_name || "ผู้รับเกียรติบัตร"}`;
            previewImage.dataset.fallbackSrc = `/api/public/certificates/${encodedCode}/download?format=png`;
            previewImage.src = `/api/public/certificates/${encodedCode}/image`;
            downloadPdf.href = `/api/public/certificates/${encodedCode}/download?format=pdf`;
            downloadJpg.href = `/api/public/certificates/${encodedCode}/download?format=jpg`;
            downloadPng.href = `/api/public/certificates/${encodedCode}/download?format=png`;
        }
    }

    previewImage.addEventListener("load", () => {
        preview.hidden = false;
        downloads.hidden = false;
    });

    previewImage.addEventListener("error", () => {
        if (previewImage.dataset.fallbackSrc) {
            const fallbackSrc = previewImage.dataset.fallbackSrc;
            delete previewImage.dataset.fallbackSrc;
            previewImage.src = fallbackSrc;
            return;
        }
        preview.hidden = true;
        downloads.hidden = true;
        previewImage.removeAttribute("src");
        details.open = true;
        setTechnicalError({
            code: "CERTIFICATE_IMAGE_UNAVAILABLE",
            message: "The certificate image could not be displayed. Please restart the application and try again.",
        });
    });

    function showFailure(error) {
        result.dataset.state = "error";
        icon.textContent = "×";
        if (error.status === 404) {
            heading.textContent = "ไม่พบเกียรติบัตร";
            summary.textContent = "ตรวจสอบรหัสแล้วลองอีกครั้ง หากรหัสมาจากคิวอาร์โค้ดโปรดติดต่อหน่วยงานผู้ออก";
        } else {
            heading.textContent = "ไม่สามารถตรวจสอบได้";
            summary.textContent = "ระบบไม่สามารถดำเนินการได้ในขณะนี้ กรุณาลองใหม่";
        }
        setTechnicalError(error);
    }

    async function verify(code) {
        const normalized = code.trim().toUpperCase();
        if (!normalized) {
            input.focus();
            return;
        }
        input.value = normalized;
        resetResult();
        button.disabled = true;
        button.textContent = "กำลังตรวจสอบ…";
        heading.textContent = "กำลังตรวจสอบ";
        summary.textContent = "กำลังเชื่อมต่อกับระบบ PKRU Electronic Certificate";
        icon.textContent = "…";
        try {
            const certificate = await window.api.request(`/api/public/certificates/${encodeURIComponent(normalized)}`, { auth: false });
            showCertificate(certificate);
            const url = new URL(window.location.href);
            url.searchParams.set("code", normalized);
            window.history.replaceState({}, "", url);
        } catch (error) {
            showFailure(error);
        } finally {
            button.disabled = false;
            button.textContent = "ตรวจสอบ";
            result.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
        }
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        verify(input.value);
    });

    document.getElementById("verify-another").addEventListener("click", () => {
        result.hidden = true;
        input.value = "";
        input.focus();
        const url = new URL(window.location.href);
        url.searchParams.delete("code");
        window.history.replaceState({}, "", url);
    });

    const queryCode = new URLSearchParams(window.location.search).get("code");
    if (queryCode) {
        verify(queryCode);
    }
})();
