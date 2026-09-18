// Состав инструментов: только просмотр. Встроенные инструменты задаются кодом сервиса,
// внешние — подключениями MCP.

import { chip, el, table } from "./dom.js";

export const tools = {
  path: "/sessions/tools",
  count: (data) => data.tools.length,
  render(data) {
    return table(
      ["Инструмент", "Источник", "Описание для модели"],
      data.tools.map((t) => [
        el("code", {}, t.name),
        t.source === "mcp" ? chip("MCP", "information") : chip("встроенный"),
        el("div", { class: "description" }, t.description),
      ]),
      "Инструментов нет.",
    );
  },
};
