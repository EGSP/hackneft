// Обращения к API ИИ-сервиса. Страница открывается с того же адреса, что и API, поэтому пути
// относительные.

const BASE = "../api";

/** Текст отказа: сообщение службы либо перечень ошибок разбора запроса (код 422). */
function describe(body, status) {
  if (body?.message) return body.message;
  if (Array.isArray(body?.detail)) {
    return body.detail
      .map((item) => `${item.loc.filter((part) => part !== "body").join(".")}: ${item.msg}`)
      .join("; ");
  }
  return `код ${status}`;
}

export async function request(method, path, body) {
  const response = await fetch(path.startsWith("/") ? BASE + path : path, {
    method,
    headers: body === undefined
      ? { Accept: "application/json" }
      : { Accept: "application/json", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(describe(data, response.status));
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  patch: (path, body) => request("PATCH", path, body),
  remove: (path) => request("DELETE", path),
};
