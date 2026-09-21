(function createApiClient(global) {
    "use strict";

    const TOKEN_KEY = "pkru_access_token";

    class ApiError extends Error {
        constructor(status, detail) {
            const safeDetail = detail && typeof detail === "object" ? detail : {};
            super(typeof safeDetail.message === "string" ? safeDetail.message : "The request could not be completed.");
            this.name = "ApiError";
            this.status = status;
            this.code = typeof safeDetail.code === "string" ? safeDetail.code : "HTTP_ERROR";
            this.correlationId = typeof safeDetail.correlation_id === "string" ? safeDetail.correlation_id : null;
        }
    }

    function token() {
        return global.localStorage.getItem(TOKEN_KEY);
    }

    function setToken(value) {
        global.localStorage.setItem(TOKEN_KEY, value);
    }

    function clearToken() {
        global.localStorage.removeItem(TOKEN_KEY);
    }

    async function request(path, options) {
        const config = Object.assign({ auth: true, responseType: "json" }, options || {});
        const headers = new Headers(config.headers || {});
        let body = config.body;

        if (config.auth) {
            const accessToken = token();
            if (accessToken) {
                headers.set("Authorization", `Bearer ${accessToken}`);
            }
        }

        const isNativeBody = body instanceof FormData || body instanceof URLSearchParams || body instanceof Blob || typeof body === "string";
        if (body !== undefined && body !== null && !isNativeBody) {
            headers.set("Content-Type", "application/json");
            body = JSON.stringify(body);
        }

        const response = await global.fetch(path, {
            method: config.method || "GET",
            headers,
            body,
            signal: config.signal,
        });

        if (!response.ok) {
            let payload = null;
            try {
                payload = await response.json();
            } catch (_error) {
                payload = null;
            }
            const detail = payload && payload.error ? payload.error : {
                code: "HTTP_ERROR",
                message: "The request could not be completed.",
            };
            if (response.status === 401 && config.auth) {
                clearToken();
            }
            throw new ApiError(response.status, detail);
        }

        if (config.responseType === "blob") {
            return response.blob();
        }
        if (config.responseType === "text") {
            return response.text();
        }
        if (config.responseType === "response") {
            return response;
        }
        if (response.status === 204) {
            return null;
        }
        return response.json();
    }

    async function login(username, password) {
        const body = new URLSearchParams();
        body.set("username", username);
        body.set("password", password);
        const result = await request("/login", {
            method: "POST",
            body,
            auth: false,
        });
        setToken(result.access_token);
        return result;
    }

    async function download(path, filename) {
        const blob = await request(path, { responseType: "blob" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        global.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    global.api = Object.freeze({
        ApiError,
        clearToken,
        download,
        login,
        request,
        token,
    });
})(window);
