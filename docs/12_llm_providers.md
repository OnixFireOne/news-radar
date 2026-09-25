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
