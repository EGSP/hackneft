// Страница настроек ИИ-сервиса: вкладки справочников, загрузка данных и сообщения о результате
// действий. Сами справочники описаны в модулях разделов.

import { api } from "./api.js";
import { status } from "./dom.js";
import { mcp } from "./mcp.js";
import { models } from "./models.js";
import { providers } from "./providers.js";
import { tools } from "./tools.js";

const SECTIONS = { providers, models, mcp, tools };

const notice = document.getElementById("notice");
let noticeTimer;

function notify(message, tone = "positive") {
  clearTimeout(noticeTimer);
  notice.className = `p-notification--${tone}`;
  notice.querySelector(".p-notification__message").textContent = message;
  notice.hidden = false;
  if (tone === "positive") noticeTimer = setTimeout(() => { notice.hidden = true; }, 8000);
}

/**
 * Контекст разделов: загруженные данные всех справочников (форма модели берёт из них перечень
 * провайдеров), перезагрузка и выполнение действия с показом отказа.
 */
const ctx = {
  data: {},
  async reload(message) {
    await load();
    if (message) notify(message);
  },
  async run(action) {
    document.body.classList.add("is-busy");
    try {
      await action();
    } catch (error) {
      notify(error.message, "negative");
    } finally {
      document.body.classList.remove("is-busy");
    }
  },
};

async function checkHealth() {
  const health = document.getElementById("health");
  const ok = await fetch("../health").then((r) => r.ok, () => false);
  const label = ok ? status("ok", "Сервис работает") : status("unreachable", "Сервис недоступен");
  label.id = "health";
  health.replaceWith(label);
}

async function load() {
  checkHealth();
  const entries = Object.entries(SECTIONS);
  const results = await Promise.allSettled(entries.map(([, section]) => api.get(section.path)));
  const problems = [];
  entries.forEach(([id], i) => {
    if (results[i].status === "fulfilled") ctx.data[id] = results[i].value;
    else problems.push(`${SECTIONS[id].path}: ${results[i].reason.message}`);
  });
  // Разделы отрисовываются после загрузки всех данных: форма раздела может опираться на
  // данные соседнего.
  for (const [id, section] of entries) {
    if (!(id in ctx.data)) continue;
    document.getElementById(id).replaceChildren(...[section.render(ctx.data[id], ctx)].flat());
    document.querySelector(`[data-count="${id}"]`).textContent = `(${section.count(ctx.data[id])})`;
  }
  if (problems.length) notify(`Данные не получены: ${problems.join("; ")}`, "negative");
}

function selectTab(id) {
  if (!(id in SECTIONS)) id = "providers";
  for (const link of document.querySelectorAll("[data-tab]")) {
    link.setAttribute("aria-selected", String(link.dataset.tab === id));
  }
  for (const section of Object.keys(SECTIONS)) {
    document.getElementById(section).hidden = section !== id;
  }
}

for (const link of document.querySelectorAll("[data-tab]")) {
  link.addEventListener("click", () => {
    location.hash = link.dataset.tab;
  });
}
window.addEventListener("hashchange", () => selectTab(location.hash.slice(1)));
document.getElementById("refresh").addEventListener("click", () => ctx.run(() => ctx.reload("Данные обновлены")));
notice.querySelector(".p-notification__close").addEventListener("click", () => { notice.hidden = true; });

selectTab(location.hash.slice(1));
load();
