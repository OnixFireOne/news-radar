# 12. LLM-провайдеры: каталог, протоколы, маршрутизатор

`llm_core/catalog.py` загружает JSON только по пути, переданному хостом.
`load_catalog(path)` проверяет все профили; `resolve_active(catalog, names, env)`
проверяет каждый выбранный профиль, включая ключ и `TODO(unverified)`.
Список `names` задаёт приоритет. Ключ по умолчанию — `LLM_KEY_<PROVIDER>`:
для `aiprime.messages` это `LLM_KEY_AIPRIME`; профиль может указать `api_key_env`.
`auth_style: "none"` ключа не требует. Сам пакет окружение не читает.

`ProviderRouter(active, timeout)` предоставляет `candidates()`, `primary()` и
`model_for(task, provider=None)`. В `provider` передаётся `ActiveProvider`.
Вызов `await router.complete(task, messages, temperature=0.3, max_tokens=None,
extra_payload=None)` берёт модель из `models[task]` либо `models["default"]`
первого профиля. Транспорты создаются лениво, failover пока отсутствует.
Единственная диспетчеризация протокола — `create_transport()`.

Поддерживаются `chat_completions` и `messages`. Первый сохраняет прежний формат
запроса и допустимость пустого текста. Второй выносит system-сообщения в `system`,
удаляет `chat_template_kwargs` и требует `max_tokens` в профиле.
`LLMEmptyResponseError` содержит `stop_reason`, `usage`, `raw_usage`, `cost_usd`
и полный `response`, чтобы хост мог учесть оплаченный пустой ответ.
Оба транспорта используют прежнюю политику retry: четыре попытки при timeout,
429 и 5xx; `Retry-After` имеет приоритет.

Цена в `LLMResponse` имеет источник `provider`, `table` или `none`.
Для таблицы нужны оба счётчика токенов; отсутствующие либо два нуля дают `None`.
`input_overhead` информационный и из токенов не вычитается.
`gpu_lock`, `chat_template_kwargs` и `max_concurrency` — свойства профиля для хоста;
роутер сам блокировки GPU и ограничения параллелизма не включает.

## Подключение в приложении

Analyzer и API создают клиент через `build_llm_client()`. Пустая или отсутствующая
`LLM_PROVIDERS` сохраняет legacy-режим: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`
и переключатель `llm_local_mode`. Ключ `LLM_API_KEY` теперь читается корректно.

В режиме каталога `LLM_PROVIDERS=aiprime.messages,local` задаёт порядок профилей.
Вызовы идут только первому, автоматического failover пока нет. Ключ берётся из
`LLM_KEY_<PROVIDER>`, где PROVIDER — часть имени до точки в верхнем регистре:
`LLM_KEY_AIPRIME` для обоих профилей aiprime. Ключ получают в панели прокси.
Каталог по умолчанию — `/app/config/providers.json`; `LLM_PROVIDERS_FILE` может
задать другой путь. Нечитаемый или невалидный выбранный файл вызывает ошибку,
возврата к встроенному каталогу нет. Провайдеры применяются при запуске процесса.
Модель выбирается по задаче (`default`, `digest`), с возвратом к `default`, если
маршрута нет. В settings.json моделей больше нет.

В каталоге `gpu_lock` управляет блокировкой, а `chat_template_kwargs` — отправкой
локальной опции отключения thinking. `llm_local_mode` в этом режиме игнорируется.
Блокировки остаются в `analyzer/llm_client.py` ради совместимости импортов и
monkeypatch; helper для thinking вынесен в `analyzer/llm_local.py`.

Для нового облака:

1. Запустить пробу в контейнере, подставив адрес, модель и имя переменной ключа:
   `docker compose run --rm --no-deps analyzer python -m llm_core.probe --base-url https://your-proxy.example.com/v1 --model your-model --key-env LLM_KEY_NEW --profile-name new`.
2. Перенести значения из draft отчёта в плоский профиль `config/providers.json`,
   добавить `name`, проверить цены и заменить все `TODO(unverified)`.
3. Добавить ключ `LLM_KEY_NEW` в окружение и имя профиля в `LLM_PROVIDERS`,
   затем перезапустить процесс.

Цены aiprime (sonnet $2/$10, opus $5/$25 за миллион вход/выход) сверены с выгрузкой
расходов прокси за 25.09: `Billed Cost` совпадает до цента, добавка ~1300 входных
токенов на вызов оплачивается. Незаполненное поле (`TODO(unverified)`) в любом
выбранном профиле роняет запуск с именем поля — так и должно быть для нового облака.
Для messages задан `max_tokens=8192`: analyzer вызывает клиент без лимита,
а 1024 из черновика пробы обрезало бы дайджесты. При пустом messages-ответе
клиент учитывает токены и стоимость, пишет WARNING с причиной остановки и
возвращает пустую строку для прежней обработки downstream. Каждый вызов пишет
INFO с провайдером, моделью, токенами и ценой (`n/a`, если она неизвестна).

## Структурированный ответ (tool use)

`LLMRequest.tool` (`JsonSchemaTool`: имя, описание, JSON Schema) необязателен.
С ним `messages` шлёт `tools` + `tool_choice: {"type": "tool"}`, а `chat_completions` —
функцию и принудительный `tool_choice`. Без него запрос байт-в-байт прежний.
В `LLMResponse.structured` попадает только вызов **нашего** инструмента; имена всех
вызванных инструментов — в `tool_calls_seen`. Если модель вызвала чужой инструмент
или ответила текстом, вызывающий код сам решает, разбирать ли JSON из текста.
Ответ только с вызовом инструмента (без текста) пустым не считается.

Проверено 25.09: на OpenRouter принудительный инструмент работает у haiku 4.5 и
sonnet 5; **opus 5.5 отвечает 400** (`tool_choice` типа `tool`/`any` не поддерживается) —
для него только текстовый режим. На aiprime tool use ненадёжен (грабля 23).

## Профили OpenRouter

`openrouter.messages` (основной) и `openrouter.chat_completions`, ключ
`LLM_KEY_OPENROUTER` (https://openrouter.ai/keys). Проба 25.09: скрытой добавки
входных токенов нет («reply with ok» = 14 токенов против 1317 у aiprime);
`chat_completions` отдаёт `usage.cost` (`cost_source: provider`), `messages` — нет,
поэтому там таблица цен из листинга `/models`. id моделей — с префиксом вендора
(`anthropic/claude-haiku-4.5`). Модели по задачам: `default` sonnet 5, `digest`
opus 5, `classify` haiku 4.5 (выбрана замером, см. ниже).

## Классификатор ценности и замер моделей

`analyzer/value_classifier.py`: порт `ValueClassifier` (`classify(items)` → исход на
каждый элемент, по порядку) и реализация `LLMValueClassifier` на задаче `classify`.
Статьи уходят батчами (по умолчанию 5: у aiprime каждый вызов стоит ~1300 лишних
входных токенов), каждая — в рамке `<<<ARTICLE id="…">>> … <<<END ARTICLE>>>`,
маркеры внутри текста экранируются. Ответ проверяется строго (перечисления,
целые 1–10, bool); битый ответ модели — ошибка у элементов, а не исключение.
Промпт — `AI_VALUE_MESSAGE_PROMPT`, версия в `AI_VALUE_PROMPT_VERSION`.
В пайплайн классификатор подключается в шаге 5 (воронка).

Замер на golden set:

```bash
docker compose run --rm --no-deps analyzer python tests/eval_value_scoring.py \
  --provider openrouter.messages --model anthropic/claude-haiku-4.5 --batch-size 5
```

Параметры: `--structured tool|text`, `--threshold` (порог «ценно», по умолчанию 5),
`--limit`. Печатает точность, нарушения «хайп ≥ 8», состязательные примеры,
токены, цену (`null`, если цена хоть одного вызова неизвестна), задержку
(медиана/p95), сломанные ответы. Каждый прогон — JSON в `tests/golden/runs/` и
строка в `tests/golden/RESULTS.md`. Правило выбора модели — р.6.1.2 спеки: самая
дешёвая из прошедших критерии р.9.

## Повадки модели: `model_params`

Поле профиля `model_params` — словарь «id модели → параметры запроса», которые
вливаются в тело запроса этой модели; `null` удаляет ключ. Пример (`openai.chat_completions`):
`"gpt-6-luna": {"temperature": null, "reasoning_effort": "none"}` — GPT-6 не принимает
температуру, отличную от 1, а function tools в `/chat/completions` разрешает только без
рассуждений. Работает в обоих протоколах. Профиль `openai.chat_completions`, ключ
`LLM_KEY_OPENAI` (https://platform.openai.com/api-keys), заведён для сравнения моделей;
в маршруте `classify` осталась haiku 4.5 (решение владельца 25.09).

## Decisions-модели (Jev)

`llm_core/decisions.py` — клиент для моделей, которые отвечают не текстом, а типизированными
решениями: вопросы `score` (упорядоченные уровни, 2–10), `choice` (варианты с описаниями),
`noul` (да/нет с вероятностью); в ответе — вероятности и `confidence`. Путь задаётся снаружи:
OpenRouter — `https://openrouter.ai/api` + `/alpha/decisions`, модель `~typesafe/jev-latest`,
ключ `LLM_KEY_OPENROUTER`; TypeSafe напрямую — `/v1/systemone`. Эндпоинт OpenRouter помечен
alpha. `analyzer/jev_classifier.py` — вторая реализация порта `ValueClassifier`; замер —
`tests/eval_value_scoring.py --impl jev`. На 26.09 порог не прошёл (72.2%), в маршрут не
подключён.

## Модель на задачу внутри профиля (27.09)

`LLMClient.complete()` / `complete_json()` принимают `task=` — задачу каталога на этот вызов (`None` = задача клиента, обычно `default`). Генерация дайджеста передаёт `task="digest"`, название тренда — `task="trend_name"`, md для базы знаний — `task="knowledge"` (И4; в `openai.chat_completions` → gpt-6-sol), классификатор ценности сам ходит в роутер с `task="classify"`. Модель — `models[task]` активного профиля, иначе `models.default`. В legacy-режиме (`LLM_PROVIDERS` пуст) параметр игнорируется.

Профиль `openai.chat_completions` (решение владельца 27.09): `classify` = gpt-6-luna (замер — `specs/reports/tz4-i3.md`, часть 5), `digest` = gpt-6-sol (не мерился), `default` = gpt-6-luna. Провайдер по-прежнему один на все задачи — выбирается `LLM_PROVIDERS` в `.env`.
