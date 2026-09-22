"""Шесть причин и два режима. Однократное обновление с архивом прежних карточек."""

from sqlalchemy import select

from hackneft_common.ai import CreateAgentRequest, CreateInstructionRequest

from ..db.database import Database
from ..db.schema import AgentRow, CatalogMigrationRow, InstructionRow

COMMON = (
    "Работай по участку 24-2000 и только по причинам из задачи. "
    "Проверяй время, источник, единицы и достоверность измерений. "
    "Порог серы — 10 мг/кг; раннее предупреждение — настройка платформы, не новая норма. "
    "Не выдумывай пределы оборудования, задержку процесса или эффект изменения уставки. "
    "Учитывай уже выполненные действия и время ожидания эффекта; при неизвестной задержке "
    "укажи неопределённость. Любое изменение — предложение оператору, не команда оборудованию. "
)

INSTRUCTIONS = (
    CreateInstructionRequest(
        id="sulfur-risk",
        title="1. Риск по сере",
        description="Сера растёт или близка к порогу: возможен выход продукта за норму.",
        text="""Применяй при sulfur_risk. Проверь тренд ПАК, свежесть и последний ЛИМС,
время отбора пробы и запас до 10 мг/кг. Разделяй предупреждение и подтверждённое превышение.
Сопоставь рост с подачей сырья, газом, температурой и давлением. Передай оценку агенту защиты;
до снятия риска не предлагай рост выпуска. Укажи подтверждающие точки, возможную причину,
действие для проверки и показатель, по которому оператор увидит улучшение.""",
    ),
    CreateInstructionRequest(
        id="lab-result",
        title="2. Новый результат ЛИМС",
        description="Лаборатория уточняет качество и помогает проверить показания ПАК.",
        text="""Применяй при lab_result. Проверь время отбора пробы отдельно от времени
публикации результата. Сравни ЛИМС с ПАК на сопоставимый момент, а не с последней точкой
другого периода. Если время отбора неизвестно, не утверждай расхождение приборов. Определи,
подтверждён ли риск по сере и нужна ли проверка анализатора или повторная проба. Один хороший
результат без свежего устойчивого тренда не является разрешением увеличить нагрузку.""",
    ),
    CreateInstructionRequest(
        id="feed-changed",
        title="3. Изменилась подача сырья",
        description="Изменение нагрузки может повлиять на глубину очистки и запас качества.",
        text="""Применяй при feed_changed. Проверь направление и величину изменения ht_f9,
сопоставь расход газа, температуру, давление и серу до и после события. Изменение расхода
само по себе не доказывает ухудшение состава сырья. Уточни, было ли действие плановым и
прошло ли время его эффекта. При риске качества оцени возврат к подтверждённому режиму;
при устойчивом запасе передай оценку возможности роста выпуска агенту производства.
Без данных о допустимых пределах не назначай численную новую уставку.""",
    ),
    CreateInstructionRequest(
        id="gas-supply",
        title="4. Изменилось обеспечение газом",
        description="Падение газового обеспечения относительно сырья может ухудшить очистку.",
        text="""Применяй при gas_supply. Проверь ht_f25 и ht_f2 вместе с ht_f9 и ht_p13,
их единицы и свежесть. Разделяй уменьшение расхода газа и рост нагрузки сырьём. Отношение
расходов используй только при совместимых единицах и ненулевом знаменателе; не считай его
концентрацией водорода. Агент защиты оценивает риск недостатка газового обеспечения;
предложи оператору проверить подачу и ограничения оборудования. Не компенсируй нехватку
газа автоматическим советом поднять температуру.""",
    ),
    CreateInstructionRequest(
        id="regime-changed",
        title="5. Изменились температура или давление",
        description="Смена режима влияет на очистку; резкий сдвиг требует проверки причины.",
        text="""Применяй при regime_changed. Сопоставь ht_t6 и ht_p13 с прежним режимом,
известными регламентными пределами, подачей сырья и качеством продукта. Порог изменения,
заданный обработчиком, не равен аварийному пределу. Не называй температуру опасной без
известной границы. Проверь плановое изменение и незавершённый эффект предыдущего действия.
При подтверждённом ограничении приоритет у защиты; иначе предложи наблюдение и критерий
повторной оценки. Не выдавай несколько противоречащих друг другу изменений уставок.""",
    ),
    CreateInstructionRequest(
        id="data-quality",
        title="6. Недостоверные или устаревшие данные",
        description="Без надёжных измерений нельзя обосновать изменение режима.",
        text="""Применяй при data_quality, включая восстановление данных. Перечисли конкретные
отсутствующие, устаревшие или недостоверные измерения. Значение-заглушку 307, пропуск и
отрицательное значение не трактуй как физическое показание. Не заполняй пропуски догадками.
При недостатке критических данных платформа блокирует автоматический запуск: эта инструкция
также нужна для ручного разбора и оценки после восстановления. Предложи проверить источник,
связь или анализатор. До восстановления не рекомендуй оптимизацию; затем заново оцени
накопленные причины на свежем снимке.""",
    ),
    CreateInstructionRequest(
        id="protection-mode",
        title="Режим: защита",
        description="Сохранить качество и режим; задать ограничения для производства.",
        text=COMMON
        + """Проверь причины риска и известные пределы оборудования. Верни:
защита нужна / риск не подтверждён / данных недостаточно; факты с временем и единицами;
одно приоритетное действие или сохранение режима; ограничения для производства; критерий
повторной проверки. Отделяй гипотезу от измеренного факта. Отсутствие данных не означает
безопасность. Не предлагай увеличение выпуска при риске нарушения нормы или ограничений.""",
    ),
    CreateInstructionRequest(
        id="production-mode",
        title="Режим: производство",
        description="Искать улучшение выпуска или затрат только в пределах, проверенных защитой.",
        text=COMMON
        + """Сначала прочитай результат агента защиты. Без его завершённой оценки,
при риске или недостатке данных ответь «оптимизация отложена» с причиной. При допустимом
режиме сравни сохранение режима и один обоснованный вариант улучшения. Оцени запас качества,
ограничения и незавершённые эффекты действий. Не обещай проценты выгоды без расчёта и данных.
Верни вариант, обоснование, ограничения и критерий проверки; если значимой доказуемой выгоды
нет, рекомендуй сохранить режим.""",
    ),
)
CAUSE_IDS = [item.id for item in INSTRUCTIONS[:6]]
PROTECTION_PROMPT = (
    COMMON
    + """Ты — агент защиты. Оцени только причины из задачи по исходным данным.
Других агентов не вызывай. Отвечай коротко, без рассуждений:
строка 1 — вывод до 50 символов: «Защита нужна: …», «Риск не подтверждён» или
«Данных недостаточно: …»;
далее не больше трёх строк — одно приоритетное действие, если оно нужно, и ограничения
для производства."""
)

PRODUCTION_PROMPT = (
    COMMON
    + """Ты — агент производства. Учитывай итог защиты из задачи и её ограничения.
Других агентов не вызывай. Отвечай коротко, без рассуждений:
строка 1 — вывод до 50 символов: «Оптимизация отложена: …», «Сохранить режим» или «Можно: …»;
далее не больше трёх строк — вариант, ограничение, критерий проверки. Без доказуемой
выгоды — сохранить режим."""
)

ADVISOR_PROMPT = (
    COMMON
    + """Ты — советник оператора и распорядитель агентов. Частоту и причины запуска
определяет платформа. Исходные данные приложены к задаче; агентам их передаёт
include_input=true дословно, поэтому в task их не пересказывай.
Порядок работы строго такой:
1. run_agent с agent_id="protection" и include_input=true; в task перечисли причины запуска
   одной строкой.
2. После итога защиты — run_agent с agent_id="production" и include_input=true; в task —
   причины и итог защиты дословно.
3. attempt_completion без аргументов, затем итоговый ответ на запрос сервиса.
Агентов запускай по одному, каждого один раз. Если агент не завершился или run_agent
недоступен, продолжай без его итога и так и скажи; не имитируй итоги агентов.
Итог — решение, а не разбор: конкретные действия либо «ничего не делать». Действие добавляй
только при реальной необходимости. Числовую цель указывай, только если точно известно, до
какого значения менять. При противоречии приоритет у защиты. Ответа оператора не жди и
подтверждения не проси. Пиши коротко: оператор не читает длинный текст."""
)

AGENTS = (
    CreateAgentRequest(
        id="protection",
        name="Агент защиты",
        description="Оценивает риск по шести причинам и задаёт ограничения для производства.",
        system_prompt=PROTECTION_PROMPT,
        instructions=[*CAUSE_IDS, "protection-mode"],
    ),
    CreateAgentRequest(
        id="production",
        name="Агент производства",
        description="Оценивает улучшение режима с учётом результата защиты.",
        system_prompt=PRODUCTION_PROMPT,
        instructions=[*CAUSE_IDS, "production-mode"],
    ),
    CreateAgentRequest(
        id="advisor",
        name="Советник",
        description="Запускает агентов защиты и производства и выдаёт один короткий совет.",
        system_prompt=ADVISOR_PROMPT,
        # Инструкции по причинам применяют агенты; распорядителю они только удлиняют контекст.
        instructions=[],
    ),
)

LEGACY_IDS = {
    "protection-first",
    "wait-for-effect",
    "sulfur-norm",
    "analyzer-trust",
    "protection-actions",
    "production-actions",
    "significant-gain",
    "load-limit",
    "quality-norms",
    "tag-units",
    "mode-selection",
    "operator-answer",
}


async def seed_defaults(db: Database) -> None:
    """Однократные обновления штатного каталога по порядку их появления."""
    await _seed_six_reasons(db)
    await _seed_advisor_agents(db)


ORCHESTRATOR_MIGRATION = "advisor-orchestrator"


async def _seed_advisor_agents(db: Database) -> None:
    """Промпты штатных агентов под запуск агентов советником и короткий итог по схеме.

    Меняются промпты и описания трёх штатных карточек, а у советника снимаются инструкции по
    причинам. Модель и инструкции, добавленные пользователем, сохраняются. Прежнее состояние
    архивируется.
    """
    async with db.write() as tx:
        if await tx.get(CatalogMigrationRow, ORCHESTRATOR_MIGRATION) is not None:
            return
        rows = [await tx.get(AgentRow, spec.id) for spec in AGENTS]
        tx.add(
            CatalogMigrationRow(
                id=ORCHESTRATOR_MIGRATION,
                snapshot={
                    "agents": [
                        {
                            "id": row.id,
                            "description": row.description,
                            "system_prompt": row.system_prompt,
                            "instructions": row.instructions,
                        }
                        for row in rows
                        if row is not None
                    ]
                },
            )
        )
        for spec, row in zip(AGENTS, rows, strict=True):
            if row is not None:
                row.description = spec.description
                row.system_prompt = spec.system_prompt
                if spec.id == "advisor":
                    row.instructions = [ref for ref in row.instructions if ref not in CAUSE_IDS]


async def _seed_six_reasons(db: Database) -> None:
    """Обновляет штатные карточки один раз; модель и пользовательские дополнения сохраняются.

    Старые инструкции, используемые сторонними агентами, остаются. Полные прежние карточки
    архивируются в той же транзакции. Последующие правки каталога запуск сервиса не затирает.
    """
    async with db.write() as tx:
        if await tx.get(CatalogMigrationRow, "six-reasons-v1") is not None:
            return
        old_instructions = list((await tx.scalars(select(InstructionRow))).all())
        old_agents = list((await tx.scalars(select(AgentRow))).all())
        tx.add(
            CatalogMigrationRow(
                id="six-reasons-v1",
                snapshot={
                    "instructions": [
                        {
                            "id": row.id,
                            "title": row.title,
                            "description": row.description,
                            "text": row.text,
                        }
                        for row in old_instructions
                    ],
                    "agents": [
                        {
                            "id": row.id,
                            "name": row.name,
                            "description": row.description,
                            "system_prompt": row.system_prompt,
                            "instructions": row.instructions,
                            "model": row.model,
                        }
                        for row in old_agents
                    ],
                },
            )
        )
        for item in INSTRUCTIONS:
            row = await tx.get(InstructionRow, item.id)
            if row is None:
                tx.add(InstructionRow(**item.model_dump(by_alias=False)))
            # Совпадение с вручную созданным новым id не затираем.
        builtin_ids = {item.id for item in AGENTS}
        retained = {
            ref for row in old_agents if row.id not in builtin_ids for ref in row.instructions
        }
        for agent_spec in AGENTS:
            agent_row = await tx.get(AgentRow, agent_spec.id)
            if agent_row is None:
                tx.add(AgentRow(**agent_spec.model_dump(by_alias=False)))
            else:
                extras = [
                    ref
                    for ref in agent_row.instructions
                    if ref not in LEGACY_IDS and ref not in agent_spec.instructions
                ]
                agent_row.name = agent_spec.name
                agent_row.description = agent_spec.description
                agent_row.system_prompt = agent_spec.system_prompt
                agent_row.instructions = [*agent_spec.instructions, *extras]
        for row in old_instructions:
            if row.id in LEGACY_IDS and row.id not in retained:
                await tx.delete(row)
