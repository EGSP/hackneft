// Справочник провайдеров: просмотр, добавление, правка, проверка связи и удаление карточек.

import { api } from "./api.js";
import {
  actions, button, checked, el, keyValueEditor, keyValues, muted, openDialog, selectField,
  status, table, textField, toolbar,
} from "./dom.js";

const DEFAULT_TYPE = "openai_compatible";

const TYPES = [
  ["openai_compatible", "Локальный OpenAI-совместимый (vLLM, Ollama, LM Studio)"],
  ["yandex", "Yandex AI Studio"],
];

const MASK_HELP = "Значение «••••••» оставляет прежний секрет.";

/**
 * Секреты по типу провайдера: ключи, которые предлагаются новой карточке (`initial`), все
 * допустимые ключи (`all`), подсказки к значениям и пояснение под списком.
 */
const SECRETS = {
  openai_compatible: {
    initial: ["OPENAI_BASE_URL", "OPENAI_API_KEY"],
    all: ["OPENAI_BASE_URL", "OPENAI_API_KEY"],
    placeholders: {
      OPENAI_BASE_URL: "http://host.docker.internal:11434/v1",
      OPENAI_API_KEY: "необязательно, если сервер не требует ключа",
    },
    help: "Обязателен OPENAI_BASE_URL — адрес API сервера моделей с суффиксом /v1. Если ИИ-сервис "
      + "запущен в Docker, а сервер моделей — на этой же машине, вместо localhost указывается "
      + `host.docker.internal. ${MASK_HELP}`,
  },
  yandex: {
    initial: ["YANDEX_FOLDER_ID", "YANDEX_KEY_ID", "YANDEX_SERVICE_ACCOUNT_ID", "YANDEX_PRIVATE_KEY"],
    all: ["YANDEX_FOLDER_ID", "YANDEX_KEY_ID", "YANDEX_SERVICE_ACCOUNT_ID", "YANDEX_PRIVATE_KEY",
      "YANDEX_IAM_TOKEN", "YANDEX_BASE_URL"],
    placeholders: {
      YANDEX_FOLDER_ID: "идентификатор каталога",
      YANDEX_KEY_ID: "идентификатор авторизованного ключа",
      YANDEX_SERVICE_ACCOUNT_ID: "идентификатор сервисного аккаунта",
      YANDEX_PRIVATE_KEY: "закрытый ключ целиком, с переносами строк",
      YANDEX_IAM_TOKEN: "готовый IAM-токен вместо ключа сервисного аккаунта",
    },
    help: "Обязателен YANDEX_FOLDER_ID и ключ сервисного аккаунта либо YANDEX_IAM_TOKEN. "
      + `Закрытый ключ вставляется целиком, с переносами строк. ${MASK_HELP}`,
  },
};

// Тип указывается первым: от него зависят предлагаемые ключи секретов.
function providerForm(provider) {
  const type = provider?.type ?? DEFAULT_TYPE;
  const secrets = keyValueEditor("Секреты", {
    entries: provider
      ? Object.entries(provider.secrets)
      : SECRETS[type].initial.map((key) => [key, ""]),
    keys: SECRETS[type].all,
    placeholders: SECRETS[type].placeholders,
    help: SECRETS[type].help,
  });
  const body = [
    selectField("Тип", {
      name: "type",
      value: type,
      options: TYPES,
      onchange: (event) => secrets.useKeys(SECRETS[event.target.value]),
    }),
    textField("Имя", {
      name: "name",
      value: provider?.name ?? "",
      required: !provider,
      readonly: Boolean(provider),
      pattern: "[a-z][a-z0-9_\\-]{0,39}",
      placeholder: "local-llm",
      help: provider
        ? "Имя не меняется: по нему на карточку ссылаются модели."
        : "Латиница в нижнем регистре, цифры, «_» и «-». После создания не меняется.",
    }),
    textField("Название", {
      name: "title", value: provider?.title ?? "", placeholder: "Локальный сервер моделей",
    }),
    secrets.node,
  ];
  const read = (form) => ({
    title: form.elements.title.value.trim() || null,
    type: form.elements.type.value,
    secrets: secrets.value(),
  });
  return { body, read };
}

function create(ctx) {
  const { body, read } = providerForm(null);
  openDialog({
    title: "Новый провайдер",
    body,
    submitLabel: "Добавить",
    wide: true,
    submit: (form) => api.post("/providers", { name: form.elements.name.value.trim(), ...read(form) }),
    done: (provider) => ctx.reload(`Провайдер «${provider.name}» добавлен: ${provider.lastCheckMessage ?? ""}`),
  });
}

function edit(ctx, provider) {
  const { body, read } = providerForm(provider);
  openDialog({
    title: `Провайдер «${provider.name}»`,
    body,
    wide: true,
    submit: (form) => api.patch(`/providers/${provider.id}`, read(form)),
    done: (saved) => ctx.reload(`Провайдер «${saved.name}» сохранён: ${saved.lastCheckMessage ?? ""}`),
  });
}

async function check(ctx, provider) {
  await ctx.run(async () => {
    const result = await api.post(`/providers/${provider.id}/check`);
    await ctx.reload(`Провайдер «${provider.name}»: ${result.lastCheckMessage ?? result.checkStatus}`);
  });
}

async function remove(ctx, provider) {
  if (!confirm(`Удалить карточку провайдера «${provider.name}»?`)) return;
  await ctx.run(async () => {
    await api.remove(`/providers/${provider.id}`);
    await ctx.reload(`Провайдер «${provider.name}» удалён`);
  });
}

export const providers = {
  path: "/providers",
  count: (data) => data.providers.length,
  render(data, ctx) {
    return [
      toolbar(button("Добавить провайдера", () => create(ctx), "positive")),
      table(
        ["Имя", "Тип", "Секреты", "Состояние", "Последняя проверка", ""],
        data.providers.map((p) => [
          el("div", {}, el("strong", {}, p.name), p.title ? el("div", {}, muted(p.title)) : null),
          p.type,
          keyValues(Object.entries(p.secrets)),
          status(p.checkStatus),
          checked(p.lastCheckAt, p.lastCheckMessage),
          actions(
            button("Изменить", () => edit(ctx, p)),
            button("Проверить", () => check(ctx, p)),
            button("Удалить", () => remove(ctx, p), "negative")),
        ]),
        "Провайдеров нет.",
      ),
    ];
  },
};
