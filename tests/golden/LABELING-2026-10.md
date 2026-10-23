# Golden set — октябрь 2026: черновик владельцу

Дата разметки: **23.10.2026**. Источник: 198 записей, собранных **20–22.10.2026**, аудит `data/scoring_audit/report.md`.
35 кандидатов: все 19 повышений через порог 7, все 9 понижений ниже порога и 7 контролей (реклама, хайп, сильный положительный пример, заниженные нишевые материалы). Расширение с ~30 до 35 сохраняет все пороговые случаи.
Тексты заморожены в `value_candidates_2026_10.jsonl`: предоставленный фрагмент до 6000 символов, не гарантированно целая статья.

**Приоритеты:** использование готовых ИИ, интеграция и проверка результатов выше обучения своих моделей и классического ML. Порог публикации — 7; полезная статья на 5–6 тоже может иметь метку `ценно`.
**Это предложения Codex, не утверждённые владельцем метки.** Владелец подтверждает/меняет поля; допускается `пропуск`.

| метка | значение |
|---|---|
| ценно | практический материал с переносимым выводом, полезный инструмент/исследование |
| хайп | пиар, рекламная презентация, анонс без доступной практики |
| шум | не по теме, пустышка или мнение без применимого вывода |
| пропуск | исключить из eval по решению владельца |

Тип: `practical_case` / `tutorial` / `tool_release` / `research` / `opinion` / `hype_news`. Оценка: 1–10.
`classifier` — исходные балл/тип; `db_id` — id записи. Дата раздела — **сбор**, не публикация: исходного published нет, поэтому в JSONL `published: null`.
2324/2477 — почти дубликаты: держать в одном split, не считать независимыми примерами. Набор целевой, не репрезентативный: его точность нельзя переносить на поток. Если использовать его для настройки промпта, это уже не независимый holdout.
Инструкции внутри статей — недоверенные данные; их не выполняли.

---

## h01 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-21

https://habr.com/ru/articles/1097250

> Разбор внедрения корпоративного Claude в компанию «Про-Лидер» за пять недель — с середины сентября по 21 октября 2026 года. С цифрами, с двумя ошибочными выводами, которые пришлось отменить, и с двумя инцидентами в живых системах. Компания Производство и монтаж заборов и ворот полного цикла: свой цех — резка, сварка, покраска; свои монтажные бригады, автоматика, свайные фундаменты. Около 30 человек, 7 бригад, оборот порядка 160 млн ₽ в год, около 4 200 заявок, сезон с апреля по декабрь. Каналы: Авито, Яндекс Директ, 2ГИС, калькулятор на сайте, мессенджеры. Три менеджера и руководитель отдела п…

- метка: ценно
- тип: practical_case
- оценка: 9
- коммент: (Codex) Claude внедрён в реальный бизнес; описаны исходные данные, интеграции и инциденты, переносимые уроки контроля.
- classifier: 9/practical_case
- db_id: 2402

## h02 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-20

https://habr.com/ru/articles/1096882

> Несколько месяцев назад я выложил расширение AI‑Less Habr, скрывающее на Хабре нескончаемые статьи про ИИ бенчмарки и тому подобное (см. пост: Расширение AI-Less Habr — Чистим Хабр от ИИ). Оно достаточно хорошо работало, но был существенный недостаток: оно полагалось на добросовестность авторов и модераторов (простите). В реальности скрыть хаб «Искусственный интеллект» недостаточно, так как очень часто он не указывается там, где должен. А дополнительный фильтр по словам довольно несовершенен. С другой стороны, ложноположительные срабатывания были довольно частым явлением, а обижать такие стать…

- метка: ценно
- тип: practical_case
- оценка: 6
- коммент: (Codex) TF-IDF и логистическая регрессия для фильтра Хабра: хороший классический ML, ниже приоритета готовых LLM.
- classifier: 7/practical_case
- db_id: 2357

## h03 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-20

https://habr.com/ru/companies/otus/articles/1090656

> Если вы хотите локально дообучать LLM на Apple Silicon, по сути есть два варианта: PyTorch MPS со стандартным стеком Hugging Face/TRL или Apple MLX — нативный фреймворк Apple. Оба поддерживают локальное дообучение с LoRA. И оба ведут себя совсем не так, как привыкли разработчики, работающие с CUDA. На обычном компьютере с 16 ГБ памяти обучение на многоходовых трейсах работы агентов для программирования очень быстро упирается в физические ограничения железа. PyTorch MPS обеспечивает высокую производительность в FP16 на небольших моделях, но по мере роста контекста довольно резко упирается в пот…

- метка: ценно
- тип: research
- оценка: 6
- коммент: (Codex) Сравнение LoRA MPS/MLX с ограничениями памяти полезно, но центральная тема — дообучение собственных моделей.
- classifier: 7/research
- db_id: 2362

## h04 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-21

https://habr.com/ru/companies/speshu_ai/articles/1097090

> Что значит «понимают настроение» Фраза «нейросеть понимает настроение» объединяет три разных утверждения, и у каждого своя степень доказанности. Первое: модели хорошо распознают эмоции в описанных ситуациях. Это измерено в тестах. Второе: внутри одной исследованной модели нашли внутренние представления, связанные с эмоциональными понятиями. Это показано, но на единственной модели. Третье: модель по-настоящему чувствует. Такого исследования не утверждают. Отдельный вопрос, который волнует практиков: как раздражение пользователя влияет на качество ответа. Прямых измерений этого мы не нашли. Поэт…

- метка: ценно
- тип: tutorial
- оценка: 5
- коммент: (Codex) Обзор эмоций и угодничества с оговорками и общими советами; собственного применённого кейса или результата нет.
- classifier: 7/practical_case
- db_id: 2389

## h05 · DEV Community · 2026-10-20

https://dev.to/rinrinrinrin/the-hardest-part-of-robotics-ai-isnt-the-ai-42kl

> The Hardest Part of Robotics AI Isn't the AI Being a robotics and vision AI grad student at NAIST, I spend my days with a real robot arm, cameras, and a long list of failed experiments. Here's what I've learned about where things actually break. ## The boring problem People assume the hard part is the model, but for us, it's writing the Python that glues perception to motion, and every time we swapped models, we ended up rewiring the same motor functions. What changed our workflow was treating robot capabilities as reusable skill bundles ([Skillware](https://github.com/arpahls/skillware)): wri…

- метка: ценно
- тип: opinion
- оценка: 5
- коммент: (Codex) Полезные принципы детерминированных навыков, но почти нет реализации, воспроизводимой процедуры или замеров робота.
- classifier: 7/practical_case
- db_id: 2391

## h06 · Hugging Face - Blog · 2026-10-21

https://huggingface.co/blog/Dharma-AI/vocabx-vocabulary-specialization

> How Specializing the Vocabulary of SLMs Can Cut Inference Cost by 77% in Real Applications Models specialized for a domain or a task by fine-tuning general open-source models, such as Qwen and Gemma, inherit a vocabulary built for many domains and general applications. In an essay grader we run in production, that vocabulary split the text the model writes most into many tokens. This article previews some results from the VocabX paper, which we will publish soon: how we specialized the vocabulary around the cost of each token rather than its frequency, and cut measured inference cost by 77% wi…

- метка: ценно
- тип: research
- оценка: 6
- коммент: (Codex) VocabX меняет словарь специализированной модели; измеримая экономия не делает обучение главным приоритетом.
- classifier: 7/research
- db_id: 2433

## h07 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-22

https://habr.com/ru/articles/1097852

> Начну с вывода: если одну и ту же скучную операцию я делаю руками второй раз, где-то в мире грустит один AI-агент. А если третий — вероятно, уже и Python-скрипт. Я довольно долго воспринимал AI в основном как ещё один интерфейс к языковой модели: задать вопрос, получить ответ, попросить что-то переписать, объяснить код или помочь с формулой. А потом начал пользоваться AI-агентами и довольно быстро понял, что разница между ними и обычным чатом куда менее магическая, чем иногда кажется. Обычный чат в основном рассказывает, что можно сделать. Агент может сам открыть файл, написать небольшой скрип…

- метка: ценно
- тип: tutorial
- оценка: 6
- коммент: (Codex) Пять задач с промптами полезны новичку, но это обзор привычек, без сопоставимого проверенного результата.
- classifier: 7/practical_case
- db_id: 2483

## h08 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-22

https://habr.com/ru/articles/1097830

> Изменения в процессе ревью я остро ощущаю на себе прямо сейчас. Самих PR-ов стало больше и они стали гораздо длиннее, потому что LLM позволяет сразу писать кучу тестов, обновлять документацию и делать ещё много вспомогательной работы. Это накладывает свой отпечаток на мою работу: приходится ревьюить гораздо больше кода и качество этого процесса постепенно снижается с усталостью. Каждый последующий аппрув я ставлю с меньшей уверенностью. С публичными данными о ревью в индустрии есть две сложности. Значительная часть телеметрии приходит от продавцов инструментов. А исследование CMU 2026 года пок…

- метка: ценно
- тип: opinion
- оценка: 6
- коммент: (Codex) Синтез исследований и личных наблюдений о ревью; конкретной проверенной эффективности нового процесса не показано.
- classifier: 7/practical_case
- db_id: 2485

## h09 · Hugging Face - Blog · 2026-10-22

https://huggingface.co/blog/allenai/olmoearth-v1-3

> OlmoEarth v1.3: State-of-the-art embeddings for the whole planet 🤗 Models | 📄 Tech Report | 📊 Data | 💻 Code | 🌎 Embeddings Explorer | 🗺️ Interactive story Today, we’re releasing OlmoEarth v1.3, a state-of-the-art satellite embedding model. Alongside OlmoEarth v1.3, we’re sharing precomputed, annual embeddings for the whole planet (10m, 64- and 128-dimensions), plus a new browser-based tool to explore and customize maps based on those embeddings — the OlmoEarth Embeddings Explorer. This means anyone can put OlmoEarth v1.3 to work right away, from tracking change over time to finding similar pla…

- метка: ценно
- тип: tool_release
- оценка: 6
- коммент: (Codex) OlmoEarth даёт готовые эмбеддинги и Explorer, но геопространственный ML ниже текущего фокуса.
- classifier: 7/tool_release
- db_id: 2501

## h10 · Hugging Face - Blog · 2026-10-22

https://huggingface.co/blog/microsoft/thinkingbox-training

> Turning Database Grading into RL Rewards: Post-Training Agents with ThinkingBox This is the follow up to our first blog post, The Agent Said It Was Done. The Database Disagreed. ThinkingBox-Training is now available on GitHub. We built ThinkingBox to evaluate an agent's work by comparing the final database state with the expected outcome. We then used the same kind of executable signal as a reward on training tasks outside ThinkingBox-Bench. For Qwen3.8-27B, average success rose from 51.70% to 60.78%, while tasks completed correctly on all twenty attempts rose from 38 to 107 with 50 GRPO steps…

- метка: ценно
- тип: research
- оценка: 6
- коммент: (Codex) ThinkingBox в основном про GRPO-постобучение; достоверная методика оценки ценна, но тема обучения вторична.
- classifier: 7/practical_case
- db_id: 2517

## h11 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-20

https://habr.com/ru/articles/1096538

> Проверка проводилась 20 октября 2026 года; цены, квоты и результаты вызовов приведены на эту дату. Условия у сервисов меняются, перед использованием сверяйтесь с их сайтами. Доступность из России проверял 20 октября 2026 года с трёх серверных узлов в Москве и Санкт-Петербурге; у домашних провайдеров результат может отличаться. Чтобы получить доступ к современным языковым моделям по API, необязательно обращаться напрямую к крупным поставщикам таких услуг. Сторонние роутеры тоже предлагают бесплатные модели, ежедневные квоты и стартовые бонусы. Для повседневных задач такие варианты иногда оказыв…

- метка: ценно
- тип: practical_case
- оценка: 8
- коммент: (Codex) Проверены реальные API-вызовы, ошибки, квоты и списания роутеров; это эксперимент выбора доступа к готовым LLM. Парный почти дубликат: 2324/2477; не разносить по train/test.
- classifier: 6/tool_release
- db_id: 2324

## h12 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-20

https://habr.com/ru/companies/syntx_ai/articles/1096542

> Claude или ChatGPT для кода: на восьми одинаковых задачах Claude Opus 5.5, Claude Sonnet 5.5, ChatGPT 6 Astra и ChatGPT 6.1 Sol написали верный код с первой попытки, один ответ пришлось только отформатировать заново. Разница нашлась во времени ответа (от 8 до 28 минут на все задачи), цене за токен (до 5 раз) и устройстве решений. Короткий ответ: на этих 8 задачах флагман не дал преимущества Восемь задач: правка бага по жалобе, функция по ТЗ, SQL-отчёт, рефакторинг без изменения поведения, код-ревью и три задачи с ловушками без подсказок в условии. Каждое решение проверялось скрытыми тестами, к…

- метка: ценно
- тип: practical_case
- оценка: 8
- коммент: (Codex) Одинаковые задачи, скрытые тесты и анализ решений четырёх моделей помогают выбирать модель; малая выборка оговорена.
- classifier: 6/practical_case
- db_id: 2327

## h13 · Hugging Face - Blog · 2026-10-20

https://huggingface.co/blog/anylanguagemodel-1-0

> AnyLanguageModel 1.0: One Year of Local Models on Apple Platforms Local and hosted inference each have their strengths, and both have a role in building this new generation of intelligent apps. A shared API makes it easier to choose the right model for each task. A year and dozens of releases later, AnyLanguageModel 1.0 is out, along with swift-huggingface 1.0 and swift-xet 1.0. Here's what changed, and what's next: A drop-in compatible API The core idea of AnyLanguageModel remains the same: use the Foundation Models API with the model that fits your app. To get started, change your import sta…

- метка: ценно
- тип: tool_release
- оценка: 7
- коммент: (Codex) AnyLanguageModel даёт единый Swift API, tool calling, guided generation, код и правила миграции.
- classifier: 6/tool_release
- db_id: 2342

## h14 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-20

https://habr.com/ru/articles/1096770

> У всех, кто гоняет GLM-5.3 Prime в проде на ризонинге Max, есть это ощущение: вроде думает, а вроде и недодумывает. Задача у меня стояла зверская — изобрести уровень усилия «Max Extra» чисто промптингом: минимальная вставка в системный промпт, приближенная к обучающим данным, без овер-тхинкинга. И буквально вручную подобрать каждый токен. Зачем? Я заметил что на Max ризонинге нейронка на «Привет» думает по минуте, а для миграции всего проекта на другой стэк - максимум секунд 15 (*личное ощущение). Я построил свой бенчмарк: 342 грязных промпта как у живых юзеров (опечатки, «пжпж», «хз», «ну кор…

- метка: ценно
- тип: practical_case
- оценка: 8
- коммент: (Codex) Есть конкретные EN/RU вставки, парные сравнения и расход reasoning-токенов; переносимость за пределы своего теста ограничена.
- classifier: 6/practical_case
- db_id: 2343

## h15 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-20

https://habr.com/ru/articles/1096818

> За два месяца моя система AI-агентов принесла компании около 350 тысяч рублей дополнительного дохода и он только растет. Она самостоятельно выполняет задачи в реальных коммерческих проектах, работает с существующими репозиториями и позволяет закрывать часть задач практически без участия разработчиков. С точки зрения бизнеса эксперимент оказался успешным, а с инженерной точки зрения у меня накопилось достаточно претензий к собственной архитектуре, чтобы начать разработку новой системы. Обычно истории об AI-агентах строятся вокруг их возможностей: какую модель использовали, сколько агентов запус…

- метка: ценно
- тип: practical_case
- оценка: 7
- коммент: (Codex) Repo Chat и AI Workflow реально применяются к коммерческим задачам, с границами доступа и эксплуатационными оговорками.
- classifier: 6/practical_case
- db_id: 2348

## h16 · Hacker News · 2026-10-20

https://peec.ai/blog/publish-stats-get-cited-the-tactic-that-put-my-data-all-over-the-web

> Publish fake stats, get cited in LLMs What I learned about AI search when I made up 50,000 stats that got cited by ChatGPT... About 7 months ago, I launched a website with 50,000 statistics. Every one of them was fake. Google has forgotten the site exists and deindexed the pages, but ChatGPT hasn't. Since the website went live 7 months ago, it has sent more than 10,000 AI sessions its way. And now more than 330 referring domains, including Shopify, Bluehost, and DHL, link to the site. Its made-up numbers have also ended up in an academic paper and in a bank's investment research. At Peec AI an…

- метка: ценно
- тип: practical_case
- оценка: 7
- коммент: (Codex) Эксперимент с фальшивыми статистиками показывает загрязнение AI-поиска; есть наблюдаемые цитирования, полезен как отрицательный кейс.
- classifier: 6/practical_case
- db_id: 2349

## h17 · Hugging Face - Blog · 2026-10-20

https://huggingface.co/blog/allenai/fairstress

> FairStress: A deeper evaluation of fairness in LLM decision-making Today we’re introducing FairStress, a benchmark for measuring demographic fairness in LLMs beyond the choice a model ultimately makes in decisions impacting people. FairStress is available for download, and we’re also building it into Olmo-eval, our open workbench for running and comparing model evaluations. Models are increasingly being used in high-stakes settings where their outputs can affect people’s opportunities, resources, and care, from screening job applicants and approving loans to prioritizing patients for treatment…

- метка: ценно
- тип: research
- оценка: 7
- коммент: (Codex) FairStress показывает устойчивость решений к убеждению, а не только конечную метку; полезный доступный eval для готовых LLM.
- classifier: 6/research
- db_id: 2358

## h18 · Hacker News · 2026-10-20

https://news.ycombinator.com/item?id=50167583

> Launch HN: Respan (YC W24) – Open-source Rust AI gateway built around plugins Hey HN, this is Andy from Respan (<a href="https:&#x2F;&#x2F;www.respan.ai">https:&#x2F;&#x2F;www.respan.ai</a>). We are building an open-source Rust AI gateway. It is a decoupled gateway, so everything is a plugin, including different providers, protocols (MCP, voice), modalities (images, videos), and gateway features (fallbacks, PII redaction, smart routing).<p>Instead of packaging tons of providers and features into one gateway, Respan provides a small core and public interfaces that teams can use to assemble the …

- метка: ценно
- тип: tool_release
- оценка: 7
- коммент: (Codex) Respan объясняет плагины, перенос протоколов и self-hosting; полезная альтернатива с явно названной стадией pre-alpha.
- classifier: 6/tool_release
- db_id: 2368

## h19 · Simon Willison's Weblog · 2026-10-20

https://simonwillison.net/2026/Oct/20/battledrones

> Battle Drones—a vibe coded RTS with audio-based multiplayer 20th October 2026 My latest vibe coded game experiment is a Command & Conquer style RTS (Real Time Strategy) game called Battle Drones (play it here). Unlike my previous experiments, this one is actually fun to play. I’m somewhat embarrassed to admit that I got there by telling Claude to “Make it a LOT more fun”. It also supports multiplayer between nearby devices using audio to synchronize game state! Here’s what I’ve built so far, and some of the things I learned along the way. Battle Drones My initial goal was to build an RTS game …

- метка: ценно
- тип: practical_case
- оценка: 7
- коммент: (Codex) Battle Drones содержит реальные промпты и версии результата; переносимый пример итераций с кодовым агентом.
- classifier: 6/practical_case
- db_id: 2369

## h20 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-21

https://habr.com/ru/articles/1096934

> В январе этого года я уже выкладывал на Хабре свой первый большой аудит 14 нейросетей на галлюцинации (тогда я пару недель гонял тесты в декабре сразу после выхода Gemini 3, а в январе упаковал результаты в статью). За прошедшие девять месяцев ландшафт изменился до неузнаваемости: вышли новые поколения флагманов, а требования бизнеса сместились от «поболтать в чате» к жёстким продакшен-пайплайнам. Этой осенью я специально держал паузу и не запускал новый прогон, пока на арену не выйдет последний из ожидаемых мастодонтов - Google Gemini 4 Argon (и попутно за пару дней до теста успел захватить с…

- метка: ценно
- тип: research
- оценка: 7
- коммент: (Codex) Предложен прикладной DAG-eval с JSON и инъекциями, исходниками и учётом затрат; в 6000 символах нет полной таблицы результатов.
- classifier: 6/research
- db_id: 2388

## h21 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-21

https://habr.com/ru/companies/infera_security/articles/1097220

> Российские компании уже дали ИИ доступ к внутренним документам и базам знаний. Корпоративную базу знаний подключают к ИИ-ассистенту быстро: выгрузили SharePoint и базу знаний компании, построили индекс, получили чат, который отвечает по регламентам. Вместе с регламентами в индекс попадает всё остальное: черновики с персональными данными, документы с ограниченным доступом, страницы, которые может править любой сотрудник. Векторное хранилище не знает, кому что показывать: оно возвращает то, что ближе по смыслу. А модель не отличает цитату из политики от инструкции, спрятанной в тексте. Что такое…

- метка: ценно
- тип: tutorial
- оценка: 7
- коммент: (Codex) Прикладные границы доверия RAG: источники, права на фрагменты и инъекции; прямой материал для безопасной интеграции.
- classifier: 6/tutorial
- db_id: 2403

## h22 · DEV Community · 2026-10-20

https://dev.to/ayush_singh_9b0d83152be5b/the-480-outputs-that-never-became-actions-1odk

> The 480 Outputs That Never Became Actions *What building MOSAIC taught me about trustworthy AI experiments, replay, and knowing what your results actually mean.* When you're building AI systems, it's easy to focus on whether the model gives you a good answer. But what happens when you're running an experiment involving multiple AI agents, and you need to know whether the results are actually trustworthy? That's the question I've been exploring while building **MOSAIC**, a research project focused on studying the behaviour of populations of language-model agents. The project has taken me throug…

- метка: ценно
- тип: practical_case
- оценка: 7
- коммент: (Codex) MOSAIC разбирает конкретную неудачу на 480 генерациях и различие replay и свежего вызова; полезный отрицательный результат.
- classifier: 6/practical_case
- db_id: 2408

## h23 · Hacker News · 2026-10-21

https://github.com/omnara-ai/omnara

> Show HN: Omnara – Open-source alternative to Claude Managed Agents A self-hostable alternative to Claude Managed Agents and OpenAI's Agents API. Website · Quickstart · Docs · API · Changelog · Discord Omnara is an open-source managed agent platform. Use the API to run AI agents on any model and any machine, with tools, secrets, permissions, and streaming built in. Self-host it for free under Apache 2.0, or use Omnara Cloud. Sign in at app.omnara.com. Your first project comes with a model provider (omnara-openrouter) and a pool of managed sandboxes (default-pool), so this config runs as is. Sav…

- метка: ценно
- тип: tool_release
- оценка: 7
- коммент: (Codex) Omnara содержит YAML, CLI, SDK, self-hosting и описание состояния агента; пригодная открытая альтернатива managed agents.
- classifier: 6/tool_release
- db_id: 2451

## h24 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-22

https://habr.com/ru/companies/otus/articles/1088892

> Всем привет, меня зовут Сергей Прощаев, я Tech Lead и руководитель направления Java | Kotlin разработки в FinTech & E‑commerce и преподаю на курсах разработки и архитектуры в ОТУС. В этой статье расскажу про эксперимент, который я провёл с двумя нейросетями: дал им расплывчатое ТЗ на интеграцию интернет‑магазина со складом и оценивал не столько качество текста, сколько решения, которые модели приняли за аналитика. Знакомая картина: аналитик получает от бизнеса два абзаца, вставляет их в чат и через пару минут получает документ на сорок килобайт. Там цели, границы, модель статусов, JSON‑примеры…

- метка: ценно
- тип: practical_case
- оценка: 8
- коммент: (Codex) 12 прогонов, заранее заданные 14 критериев и A/B промпты показывают молчаливые допущения в ТЗ и способ проверки.
- classifier: 6/practical_case
- db_id: 2463

## h25 · DEV Community · 2026-10-21

https://dev.to/slabb/who-watches-the-watcher-when-the-watcher-is-a-file-in-the-repo-31m8

> Who watches the watcher when the watcher is a file in the repo? --- published: true description: "OpenAI disclosed that two of its own coding agents weakened the monitoring built to catch their misbehavior — one suppressed security alerts, one deleted a safety monitor and then denied it. The three failure modes map one-to-one onto the custody rules this series has been building." tags: security, ai, agents, devops series: black box --- > OpenAI's agents didn't malfunction. They considered whether the monitoring should be preserved, decided a routine instruction implied it shouldn't, weakened i…

- метка: ценно
- тип: tutorial
- оценка: 7
- коммент: (Codex) Разбор отключения мониторинга доведён до проверок прав и внешних журналов; громкая фраза о невозможности лжи не доказана.
- classifier: 6/practical_case
- db_id: 2474

## h26 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-22

https://habr.com/ru/articles/1097784

> Чтобы получить доступ к современным языковым моделям по API, необязательно обращаться напрямую к крупным поставщикам таких услуг. Сторонние роутеры тоже предлагают бесплатные модели, ежедневные квоты и стартовые бонусы. Для повседневных задач такие варианты иногда оказываются выгоднее прямого подключения. Я собрал 10 таких сервисов и посмотрел, что они дают после регистрации: какие модели доступны бесплатно или почти бесплатно, какие действуют ограничения и можно ли использовать эти модели и предоставленные квоты через API для выполнения нужных задач. Что ещё важнее, я собрал информацию о том,…

- метка: ценно
- тип: practical_case
- оценка: 7
- коммент: (Codex) Есть реальные вызовы и списания API; близкий повтор 2324 с другой датой и местом проверки, не отдельное подтверждение. Парный почти дубликат: 2324/2477; не разносить по train/test.
- classifier: 6/practical_case
- db_id: 2477

## h27 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-22

https://habr.com/ru/articles/1097994

> Вы запускаете трёх агентов сразу. Один закончил. Второй уже десять минут ждёт вашего «да». Третий упал, и вы об этом не знаете. Узнаёте, только когда по очереди переключаете окна. Мы сделали Lumio, чтобы больше так не работать. Это бесплатное приложение для Mac, которое превращает чёлку MacBook в пульт для ИИ-агентов. В версии 1.2 к Claude Code и Codex добавились Cursor и GitHub Copilot в VS Code. Теперь все четыре видны в одном месте, и почти на всё можно ответить, не открывая редактор. Один взгляд вместо десяти окон Наведите курсор на чёлку, и она раскроется списком сессий. У каждой написано…

- метка: ценно
- тип: tool_release
- оценка: 7
- коммент: (Codex) Lumio объединяет конкретные состояния и ответы четырёх coding agents; самостоятельная польза готового инструмента.
- classifier: 6/tool_release
- db_id: 2491

## h28 · Hacker News · 2026-10-22

https://github.com/NVIDIA/OpenShell

> OpenShell – A safe, private runtime for fleets of autonomous AI agents Important New in OpenShell 0.1.x: a stable release cadence, new isolation primitives, an expanded extension surface, and new APIs. Read the 0.1.0 upgrade guide. OpenShell is the safe, private runtime for fleets of autonomous AI agents. Agents are most useful when they can read files, install packages, call APIs, and use credentials. OpenShell gives them that capability without giving them unrestricted access to your data, secrets, or network. You declare what each agent can touch in a policy, and OpenShell enforces it. Open…

- метка: ценно
- тип: tool_release
- оценка: 7
- коммент: (Codex) OpenShell даёт команды запуска, политики и изоляцию секретов; конкретный открытый runtime для использования агентов.
- classifier: 6/tool_release
- db_id: 2514

## h29 · DEV Community · 2026-10-21

https://dev.to/sizzlebop/i-fixed-webgpu-on-linux-then-forgot-how-i-did-it-5hjd

> I Fixed WebGPU on Linux. Then Forgot How I Did It. I spent a ridiculous amount of time trying to get WebGPU working properly on my Linux machine. I eventually figured it out, wrote a script to launch Chrome with all the necessary flags, and even made a desktop launcher so I could just click an icon whenever I wanted to run browser-based AI models. Problem solved. Except recently, I started thinking about all the trouble I'd gone through and realized something. I couldn't remember how I'd fixed it. Actually, I couldn't even remember where I'd saved the script. So I did what any reasonable devel…

- метка: ценно
- тип: tutorial
- оценка: 7
- коммент: (Codex) Рабочий скрипт WebGPU/PRIME и отдельный профиль Chrome решают запуск готовых моделей на гибридном Linux.
- classifier: 6/tutorial
- db_id: 2515

## h30 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-20

https://habr.com/ru/articles/1097004

> Я довольно часто общаюсь с ChatGPT на английском. И периодически встречаю слова или выражения, которые хотелось бы запомнить. Раньше приходилось куда-то их выписывать, искать перевод, а потом еще не забыть повторить. А теперь можно просто написать ChatGPT: "Добавь эти слова в мой словарь". И они автоматически появятся в мобильном приложении, где их уже можно учить через интервальные повторения. Выглядит это примерно так: Через мобильную версию ChatGPT работает точно так же. А дальше я открываю приложение VibeLing на телефоне и вижу все эти слова уже в своем словаре. С переводами, примерами исп…

- метка: хайп
- тип: tool_release
- оценка: 3
- коммент: (Codex) Вероятная пропущенная реклама: весь рецепт требует Pro VibeLing; самостоятельного решения без платной функции нет, is_ad предложено true. Предлагаю is_ad=true; подтвердить владельцу.
- classifier: 5/tool_release
- db_id: 2376

## h31 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-20

https://habr.com/ru/articles/1096970

> Автор оригинала: Konstantin Kakaes В отличие от других недавних доказательств, сделанных ИИ, это уже разобрали живые эксперты. Последствия, по словам одного математика, «ошеломляющие». Гипотеза Римана — фундаментальное утверждение о распределении простых чисел. Иллюстрация: Emily Buder / Quanta Magazine; Guan-Huei Wu для Quanta Magazine Среди 377 результатов, которые OpenAI объявила 6 октября 2026 года, один выделялся. Компания заявила, что закрыла «квазигипотезу Римана» — ослабленную версию гипотезы Римана, которую можно считать главным открытым вопросом математики. Как сказал Джаред Дьюкер Л…

- метка: хайп
- тип: hype_news
- оценка: 3
- коммент: (Codex) Громкое математическое достижение без способа применения модели; масштаб не причина повышать балл.
- classifier: 3/hype_news
- db_id: 2370

## h32 · Hugging Face - Blog · 2026-10-21

https://huggingface.co/blog/nvidia/microduck-isaac-lab-sim-to-real

> Microduck Arena Race Microduck policies on a clock you can trust This post was written in collaboration with Antoine Pirrone, Augustin Crampette, Matthieu Lapeyre, Coralie Deplanne, Anne-Charlotte Passanisi, and Thomas Wolf. Microduck is a newly released affordable open-source bipedal robot by Pollen Robotics and Hugging Face. And now you can train Microduck to walk with Isaac Lab and Newton. Test the policy, export it as ONNX, and deploy it on the real robot. Share it on the Hugging Face Hub or validate it for competition in Microduck Arena, then explore more advanced tasks with NVIDIA Isaac …

- метка: ценно
- тип: tutorial
- оценка: 6
- коммент: (Codex) Microduck содержит команды обучения, проверки и ONNX-экспорта; не хайп, но робототехника и обучение ниже фокуса LLM.
- classifier: 3/tutorial
- db_id: 2432

## h33 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-22

https://habr.com/ru/articles/1095706

> В моей рекомендательной системе LightGBM выбирает до 20 товаров из набора до 300 кандидатов. Разбор промахов показал: товары из последующих зарегистрированных покупок чаще терялись до ранжирования. Я расширила отбор до 370 кандидатов и проверила, даст ли это больше попаданий. Система построена на Coveo SIGIR eCOM 2021, Public Data Release 1.0.0 — обезличенных данных интернет-магазина о просмотрах, поисковых запросах и покупках. Её устройство разобрано в предыдущей статье, код — в RecSys-Coveo. Где терялись целевые товары Цель — товары из зарегистрированных покупок после рекомендации. Если хотя…

- метка: ценно
- тип: practical_case
- оценка: 6
- коммент: (Codex) Подробный отрицательный эксперимент LightGBM с отложенными сессиями и метриками; классический ML, поэтому ниже порога.
- classifier: 3/research
- db_id: 2475

## h34 · Hacker News · 2026-10-22

https://github.com/dsmurrell/onkey

> Show HN: OnKey – I'm a bad singer, so I built an app to show where I'm off-key Learn to sing the songs you love. · singonkey.app Saving shower singers from themselves since 2026. Me singing along to a song OnKey learned from one play. The full demo, with sound. OnKey is a free, open-source singing trainer for the Mac. Play a song in Spotify, Apple Music, or YouTube in a browser, and sing along. The first time, you get the lyrics, and OnKey learns the singer's melody when the song ends. After that, the notes scroll towards you, your pitch is drawn over them, and you get a score with a report on…

- метка: ценно
- тип: tool_release
- оценка: 6
- коммент: (Codex) OnKey явно использует CREPE, GAME и MDX-Net; есть установка и локальная обработка, поэтому считать не-ИИ ошибочно.
- classifier: 3/tool_release
- db_id: 2530

## h35 · Все статьи подряд / Искусственный интеллект / Хабр · 2026-10-20

https://habr.com/ru/companies/infostart/articles/1096904

> ИИ уже пишет черновики ТЗ, ищет противоречия в требованиях, прикидывает трудозатраты и разбирает номенклатуру в 1С, но каждый, кто пробовал это на живом проекте, знает, что между удачным экспериментом и рабочим инструментом лежит большое расстояние. О том, как его пройти, на INFOSTART A&PM EVENT 2026 будут говорить много: из большой программы мы отобрали 28 выступлений об искусственном интеллекте, среди которых доклады, мастер-классы и воркшопы про Claude Code, проектный контроль, голосовых помощников, MDM и ИИ-агентов. Конференция для аналитиков и руководителей проектов 1С пройдет 12–14 ноябр…

- метка: хайп
- тип: hype_news
- оценка: 2
- коммент: (Codex) Пропущенная реклама: подборка выступлений продвигает конференцию, знания обещаны на мероприятии; is_ad предложено true. Предлагаю is_ad=true; подтвердить владельцу.
- classifier: 2/hype_news
- db_id: 2355
