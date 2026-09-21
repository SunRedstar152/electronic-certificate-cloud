(function initializeLogin() {
    "use strict";

    const form = document.getElementById("login-form");
    if (!form) {
        return;
    }

    const username = document.getElementById("username");
    const password = document.getElementById("password");
    const submitButton = document.getElementById("login-button");
    const alert = document.getElementById("login-alert");
    const errorCode = document.getElementById("login-error-code");
    const errorMessage = document.getElementById("login-error-message");
    const togglePassword = document.getElementById("toggle-password");

    function showError(error) {
        errorCode.textContent = error.code || "HTTP_ERROR";
        errorMessage.textContent = error.message || "The request could not be completed.";
        alert.hidden = false;
        alert.focus({ preventScroll: true });
    }

    function hideError() {
        alert.hidden = true;
        errorCode.textContent = "";
        errorMessage.textContent = "";
    }

    togglePassword.addEventListener("click", () => {
        const showing = password.type === "text";
        password.type = showing ? "password" : "text";
        togglePassword.textContent = showing ? "แสดง" : "ซ่อน";
        togglePassword.setAttribute("aria-pressed", String(!showing));
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        hideError();
        if (!form.reportValidity()) {
            return;
        }

        submitButton.disabled = true;
        submitButton.textContent = "กำลังเข้าสู่ระบบ…";
        try {
            await window.api.login(username.value.trim(), password.value);
            await window.api.request("/me");
            window.location.assign("/dashboard");
        } catch (error) {
            window.api.clearToken();
            showError(error);
            password.select();
        } finally {
            submitButton.disabled = false;
            submitButton.textContent = "เข้าสู่ระบบ";
        }
    });

    async function resumeExistingSession() {
        if (!window.api.token()) {
            return;
        }
        submitButton.disabled = true;
        submitButton.textContent = "กำลังตรวจสอบบัญชี…";
        try {
            await window.api.request("/me");
            window.location.assign("/dashboard");
        } catch (_error) {
            window.api.clearToken();
            submitButton.disabled = false;
            submitButton.textContent = "เข้าสู่ระบบ";
        }
    }

    resumeExistingSession();
})();
