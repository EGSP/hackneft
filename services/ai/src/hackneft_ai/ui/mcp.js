// Справочник подключений MCP: просмотр, добавление, импорт конфигурации mcpServers, правка,
// включение, проверка и удаление.

import { api } from "./api.js";
import {
  actions, button, checkbox, checked, chip, el, keyValueEditor, keyValues, muted, openDialog,
  selectField, status, table, textArea, textField, toolbar,
} from "./dom.js";

const MODES = [
  ["all", "Все инструменты сервера, включая новые"],
  ["except", "Все, кроме отмеченных"],
  ["selected", "Только отмеченные"],
];

function transportFields(transport) {
  const type = transport?.type ?? "stdio";
  const env = keyValueEditor("Переменные окружения", {
    entries: Object.entries(transport?.env ?? {}),
    help: "Значение «••••••» оставляет прежнее значение переменной.",
  });
  const headers = keyValueEditor("Заголовки HTTP", {
    entries: Object.entries(transport?.headers ?? {}),
    help: "Значение «••••••» оставляет прежнее значение заголовка.",
  });
  const stdio = el("div", { hidden: type !== "stdio" },
    textField("Команда", {
      name: "command",
      value: transport?.command ?? "",
      placeholder: "npx",
    }),
    textArea("Аргументы", {
      name: "args",
      value: (transport?.args ?? []).join("\n"),
      rows: 3,
      placeholder: "-y\n@modelcontextprotocol/server-filesystem\n/data",
      help: "По одному аргументу в строке.",
    }),
    textField("Рабочий каталог", {
      name: "cwd",
      value: transport?.cwd ?? "",
      help: "Пусто — каталог сервиса.",
    }),
    env.node);
  const http = el("div", { hidden: type === "stdio" },
    textField("Адрес", {
      name: "url",
      value: transport?.url ?? "",
      placeholder: "http://localhost:8000/mcp",
    }),
    headers.node);

  const types = [["stdio", "stdio — дочерний процесс"], ["http", "Streamable HTTP"]];
  if (type === "sse") types.push(["sse", "SSE (не поддерживается)"]);
  const select = selectField("Транспорт", {
    name: "transportType",
    value: type,
    options: types,
    onchange: (event) => {
      stdio.hidden = event.target.value !== "stdio";
      http.hidden = event.target.value === "stdio";
    },
  });

  function read(form) {
    const kind = form.elements.transportType.value;
    if (kind === "stdio") {
      return {
        type: "stdio",
        command: form.elements.command.value.trim(),
        args: form.elements.args.value.split("\n").map((a) => a.trim()).filter((a) => a !== ""),
        env: env.value(),
        cwd: form.elements.cwd.value.trim() || null,
      };
    }
    return { type: kind, url: form.elements.url.value.trim(), headers: headers.value() };
  }

  return { nodes: [select, stdio, http], read };
}

/** Отбор инструментов: режим и отметки. Смысл отметки зависит от режима. */
function toolSelection(connection) {
  const tools = connection.snapshot?.tools ?? [];
  const marked = {
    except: new Set(connection.excludedTools),
    selected: new Set(connection.enabledTools),
  };
  const caption = el("span", { class: "p-form__label" });
  const list = el("div", { class: "tool-list" });
  const group = el("div", { class: "p-form__group" }, caption, list);

  function show(mode) {
    group.hidden = mode === "all" || tools.length === 0;
    caption.textContent = mode === "except" ? "Исключить инструменты" : "Использовать инструменты";
    list.replaceChildren(...tools.map((tool) => checkbox(tool.name, {
      name: "tool",
      value: tool.name,
      checked: marked[mode]?.has(tool.name),
      help: tool.title ?? null,
    })));
  }

  const mode = selectField("Отбор инструментов", {
    name: "toolMode",
    value: connection.toolMode,
    options: MODES,
    help: tools.length === 0 ? "Состав сервера ещё не получен." : `На сервере инструментов: ${tools.length}.`,
    onchange: (event) => show(event.target.value),
  });
  show(connection.toolMode);

  function read(form) {
    const selected = form.elements.toolMode.value;
    const ticked = [...list.querySelectorAll("input:checked")].map((input) => input.value);
    return {
      toolMode: selected,
      excludedTools: selected === "except" ? ticked : undefined,
      enabledTools: selected === "selected" ? ticked : undefined,
    };
  }

  return { nodes: [mode, group], read };
}

function identity(connection) {
  return [
    textField("Имя", {
      name: "name",
      value: connection?.name ?? "",
      required: true,
      pattern: "[a-z][a-z0-9_]{0,23}",
      placeholder: "files",
      help: "Латиница в нижнем регистре, цифры и «_». Имя служит префиксом имён инструментов.",
    }),
    textField("Название", { name: "title", value: connection?.title ?? "" }),
  ];
}

function create(ctx) {
  const transport = transportFields(null);
  openDialog({
    title: "Новое подключение MCP",
    submitLabel: "Добавить",
    wide: true,
    body: [
      ...identity(null),
      ...transport.nodes,
      el("p", { class: "p-form-help-text" },
        "При добавлении сервис подключается к серверу и получает состав инструментов; " +
        "недостижимый сервер не добавляется."),
    ],
    submit: (form) => api.post("/mcp", {
      name: form.elements.name.value.trim(),
      title: form.elements.title.value.trim() || null,
      transport: transport.read(form),
    }),
    done: (c) => ctx.reload(`Подключение «${c.name}» добавлено: ${c.lastCheckMessage ?? ""}`),
  });
}

function edit(ctx, connection) {
  const transport = transportFields(connection.transport);
  const selection = toolSelection(connection);
  openDialog({
    title: `Подключение «${connection.name}»`,
    wide: true,
    body: [...identity(connection), ...transport.nodes, ...selection.nodes],
    submit: (form) => api.patch(`/mcp/${connection.id}`, {
      name: form.elements.name.value.trim(),
      title: form.elements.title.value.trim() || null,
      transport: transport.read(form),
      ...selection.read(form),
    }),
    done: (c) => ctx.reload(`Подключение «${c.name}» сохранено: ${c.lastCheckMessage ?? ""}`),
  });
}

function importConfig(ctx) {
  openDialog({
    title: "Импорт конфигурации mcpServers",
    submitLabel: "Импортировать",
    wide: true,
    body: [
      textArea("Конфигурация в формате JSON", {
        name: "json",
        rows: 12,
        required: true,
        placeholder: '{\n  "mcpServers": {\n    "files": {\n      "command": "npx",\n      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]\n    }\n  }\n}',
        help: "Формат тот же, что у Claude Desktop и других клиентов MCP. Каждая запись " +
          "добавляется отдельно; недостижимые серверы пропускаются.",
      }),
    ],
    submit: (form) => api.post("/mcp/import", { json: form.elements.json.value }),
    done: (result) => {
      const names = result.created.map((c) => c.name).join(", ");
      const skipped = result.skipped.length ? ` Пропущено: ${result.skipped.join("; ")}` : "";
      return ctx.reload(`Добавлено подключений: ${result.created.length} (${names}).${skipped}`);
    },
  });
}

async function toggle(ctx, connection) {
  await ctx.run(async () => {
    const c = await api.post(`/mcp/${connection.id}/toggle`, { enabled: !connection.enabled });
    await ctx.reload(`Подключение «${c.name}» ${c.enabled ? "включено" : "выключено"}`);
  });
}

async function check(ctx, connection) {
  await ctx.run(async () => {
    const c = await api.post(`/mcp/${connection.id}/check`);
    await ctx.reload(`Подключение «${c.name}»: ${c.lastCheckMessage ?? c.checkStatus}`);
  });
}

async function remove(ctx, connection) {
  if (!confirm(`Удалить подключение «${connection.name}»?`)) return;
  await ctx.run(async () => {
    await api.remove(`/mcp/${connection.id}`);
    await ctx.reload(`Подключение «${connection.name}» удалено`);
  });
}

function transportView(t) {
  if (t.type === "stdio") {
    return el("div", {},
      el("code", {}, [t.command, ...t.args].join(" ")),
      Object.keys(t.env).length ? keyValues(Object.entries(t.env)) : null);
  }
  return el("div", {},
    el("div", {}, `${t.type.toUpperCase()}: `, el("code", {}, t.url)),
    Object.keys(t.headers).length ? keyValues(Object.entries(t.headers)) : null);
}

function toolsView(c) {
  const tools = c.snapshot?.tools ?? [];
  const mode = {
    all: "все",
    except: `все, кроме ${c.excludedTools.length}`,
    selected: `отобрано ${c.enabledTools.length}`,
  }[c.toolMode];
  return el("div", {}, `${tools.length} на сервере`, el("div", {}, muted(`используются: ${mode}`)));
}

export const mcp = {
  path: "/mcp",
  count: (data) => data.connections.length,
  render(data, ctx) {
    return [
      toolbar(
        button("Добавить подключение", () => create(ctx), "positive"),
        button("Импорт mcpServers", () => importConfig(ctx))),
      table(
        ["Подключение", "Транспорт", "Инструменты", "Состояние", "Последняя проверка", ""],
        data.connections.map((c) => [
          el("div", {},
            el("strong", {}, c.name),
            c.title ? el("div", {}, muted(c.title)) : null,
            c.enabled ? null : el("div", {}, chip("выключено", "caution"))),
          transportView(c.transport),
          toolsView(c),
          el("div", {},
            status(c.checkStatus),
            c.stale ? el("div", {}, chip("состав устарел", "caution")) : null,
            c.problems.map((problem) => el("div", {}, el("small", {}, problem)))),
          checked(c.lastCheckAt, c.lastCheckMessage),
          actions(
            button("Изменить", () => edit(ctx, c)),
            button(c.enabled ? "Выключить" : "Включить", () => toggle(ctx, c)),
            button("Проверить", () => check(ctx, c)),
            button("Удалить", () => remove(ctx, c), "negative")),
        ]),
        "Подключений MCP нет.",
      ),
    ];
  },
};
