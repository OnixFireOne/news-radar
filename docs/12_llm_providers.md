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
Интеграция этого API в analyzer/API и рабочий `config/providers.json` — commit 3.
