// HTTP helpers. Login lives in an HttpOnly cookie; every write also sends the
// CSRF token from the readable "csrf_access_token" cookie (double-submit).

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = () => {};

/** Called when the session expires mid-use, so the app can show the login screen. */
export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_access_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export function writeHeaders(method) {
  return method === "GET" || method === "HEAD" ? {} : { "X-CSRF-TOKEN": csrfToken() };
}

/**
 * request("/documents") -> parsed JSON (null for 204).
 * With { raw: true } a successful Response is returned as-is (for streams).
 */
export async function request(path, { method = "GET", json, body, raw = false } = {}) {
  const init = { method, headers: writeHeaders(method), credentials: "same-origin" };
  if (json !== undefined) {
    init.body = JSON.stringify(json);
    init.headers["Content-Type"] = "application/json";
  } else if (body !== undefined) {
    init.body = body;
  }

  const res = await fetch(`/api${path}`, init);
  if (res.ok && raw) return res;
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized(data.error);
    throw new ApiError(data.error || `Request failed (${res.status})`, res.status);
  }
  return data;
}

/** Upload with progress events (fetch can't report upload progress). */
export function uploadWithProgress(file, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/documents");
    xhr.responseType = "json";
    for (const [name, value] of Object.entries(writeHeaders("POST"))) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress((e.loaded / e.total) * 100);
    };
    xhr.onload = () => {
      const data = xhr.response || {};
      if (xhr.status === 401) onUnauthorized(data.error);
      if (xhr.status >= 400) reject(new ApiError(data.error || `Upload failed (${xhr.status}).`, xhr.status));
      else resolve(data);
    };
    xhr.onerror = () => reject(new ApiError("Upload failed. Check your connection and try again.", 0));
    xhr.send(form);
  });
}

/** Parse a Server-Sent Events stream, calling onEvent(name, data) per event. */
export async function readServerSentEvents(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}
