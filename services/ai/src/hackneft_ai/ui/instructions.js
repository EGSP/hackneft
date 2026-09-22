// Справочник инструкций: таблица по названию, добавление, правка и удаление.

import { api } from "./api.js";
import { actions, button, el, openDialog, table, textArea, textField, toolbar } from "./dom.js";

function create(ctx) {
  openDialog({
    title: "Новая инструкция",
    submitLabel: "Добавить",
    wide: true,
    body: [
      textField("Идентификатор", {
        name: "id",
        required: true,
        pattern: "[a-z0-9]+(-[a-z0-9]+)*",
        placeholder: "sulfur-norm",
        help: "Строчные латинские буквы, цифры и дефис. После создания не меняется.",
      }),
      textField("Название", { name: "title", required: true }),
      textArea("Описание", { name: "description", rows: 2 }),
      textArea("Что делать", { name: "text", rows: 10, required: true }),
    ],
    submit: (form) => api.post("/instructions", {
      id: form.elements.id.value.trim(),
      title: form.elements.title.value.trim(),
      description: form.elements.description.value.trim(),
      text: form.elements.text.value.trim(),
    }),
    done: (item) => ctx.reload(`Инструкция «${item.title}» добавлена`),
  });
}

function edit(ctx, item) {
  openDialog({
    title: `Инструкция «${item.id}»`,
    wide: true,
    body: [
      textField("Название", { name: "title", value: item.title, required: true }),
      textArea("Описание", { name: "description", value: item.description || "", rows: 2 }),
      textArea("Что делать", { name: "text", value: item.text, rows: 10, required: true }),
    ],
    submit: (form) => api.patch(`/instructions/${item.id}`, {
      title: form.elements.title.value.trim(),
      description: form.elements.description.value.trim(),
      text: form.elements.text.value.trim(),
    }),
    done: () => ctx.reload(`Инструкция «${item.title}» сохранена`),
  });
}

async function remove(ctx, item) {
  if (!confirm(`Удалить инструкцию «${item.title}»?`)) return;
  await ctx.run(async () => {
    await api.remove(`/instructions/${item.id}`);
    await ctx.reload(`Инструкция «${item.title}» удалена`);
  });
}

export const instructions = {
  path: "/instructions",
  count: (data) => data.instructions.length,
  render(data, ctx) {
    return [
      toolbar(button("Добавить инструкцию", () => create(ctx), "positive")),
      table(
        ["Название", "Описание и действия", ""],
        data.instructions.map((item) => [
          el("div", {}, el("strong", {}, item.title), el("div", {}, el("code", {}, item.id))),
          el("div", {},
            el("p", {}, item.description || "Описание не задано"),
            el("details", {}, el("summary", {}, "Что делать"),
              el("div", { class: "instruction-text" }, item.text))),
          actions(
            button("Изменить", () => edit(ctx, item)),
            button("Удалить", () => remove(ctx, item), "negative")),
        ]),
        "Инструкций нет.",
      ),
    ];
  },
};
