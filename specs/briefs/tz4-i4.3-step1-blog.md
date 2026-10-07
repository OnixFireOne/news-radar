# Бриф Codex: И4.3, шаг 1 — блог «Нейронавт»: коллекция разборов и стили дайджеста

Репозиторий: `~/Documents/Ai/apps/neuronavt` (GitHub `OnixFireOne/neuronavt`), блог — в `blog/`
(Astro 6, тема AstroPaper, Tailwind 4, pagefind). Сайт — `https://neuronavt.blog/`.
Контекст и решения владельца — `~/Documents/Ai/apps/news-radar/specs/reports/tz4-i4.3.md`.

## Цель

News-radar (бот-агрегатор статей про ИИ) будет каждый день публиковать в этот блог:
- **дайджест** — обычный пост в коллекции `posts` (папка `blog/src/content/posts/_digests/`), он **должен**
  появляться в ленте, RSS, архиве и тегах — как посты владельца;
- **разборы статей** — 5–10 в день, **не должны** попадать в главную, `/posts`, RSS, архив и облако тегов.
  Для них — отдельная коллекция `reviews`, свой список `/reviews` и страница `/reviews/<slug>`,
  они находятся поиском по сайту (pagefind).

В этом шаге news-radar не трогаем: только блог — схема, страницы, стили и черновики-образцы.

## Что сделать

### 1. Коллекция `reviews` — `blog/src/content.config.ts`
Новая коллекция, файлы в `blog/src/content/reviews/**/[^_]*.md`. Схема (zod):

| Поле | Тип | Обяз. | Смысл |
|---|---|---|---|
| `title` | string | да | заголовок разбора (русский) |
| `description` | string | да | 1–2 предложения, для списка и meta |
| `pubDatetime` | date | да | время публикации |
| `tags` | string[] | нет, `[]` | теги статьи |
| `source_url` | string (url) | да | оригинал статьи |
| `source_type` | string | да | `rss` / `hackernews` / `devto` / … |
| `content_type` | enum | да | `practical_case`, `tutorial`, `tool_release`, `research`, `opinion`, `hype_news`, `other` |
| `value_score` | number 0–10 | да | оценка ценности |
| `digest` | string | нет | slug выпуска-дайджеста, напр. `2026-10-07-ai-radar` |
| `draft` | boolean | нет | как у постов |

Подписи типов (русские, единый словарь, например `src/utils/reviewTypes.ts`):
`practical_case` → «Кейс», `tutorial` → «Туториал», `tool_release` → «Инструмент», `research` → «Исследование»,
`opinion` → «Мнение», `hype_news` → «Новость», `other` → «Другое».

Коллекция `posts` и её схема **не меняются**.

### 2. Утилиты
- Сортировка/фильтр разборов по аналогии с `getSortedPosts` + `postFilter` (драфты скрыты всегда,
  будущий `pubDatetime` скрыт в проде). Отдельные функции для `reviews`; существующие не менять.
- URL разбора: `/reviews/<slug>`, slug — имя файла без расширения (вложенные папки `YYYY/MM/` в URL **не**
  попадают; имена файлов уникальны — news-radar добавляет в конец id статьи).
- Имя перехода: `review-<slug>` (только `[a-z0-9-]`, slug уже такой). Используется в трёх местах — карточка
  в списке, заголовок страницы разбора и (шаг 2) карточка в дайджесте — через **inline-стиль**
  `view-transition-name: review-<slug>`, как уже сделано у заголовка поста в
  `src/pages/posts/[...slug]/index.astro`. Так заголовок «перелетает» при переходе.

### 3. Страницы
- `src/pages/reviews/[...page].astro` — список с пагинацией (как `src/pages/posts/[...page].astro`),
  карточка `ReviewCard.astro` (новая, `Card.astro` не трогать): заголовок со `view-transition-name`,
  дата, бейдж типа, бейдж оценки (`7/10`), `description`.
  **Фильтр по типу:** ряд кнопок «Все · Кейс · Туториал · …» (только типы, которые реально есть).
  Реализация — на выбор, без новых зависимостей: статические страницы `/reviews/type/<type>/[...page]`
  или клиентский фильтр; главное — работает без перезагрузки шаблона и не ломает пагинацию.
- `src/pages/reviews/[...slug]/index.astro` — страница разбора на `PostLayout` (как страница поста):
  заголовок со `view-transition-name: review-<slug>`, дата, бейджи типа и оценки, тело md в `app-prose`,
  внизу блок ссылок: **«Читать оригинал»** (`source_url`, `target="_blank" rel="noopener"`) и,
  если есть `digest`, **«Из выпуска …»** → `/posts/<digest>`. Теги — простым списком текста
  **без ссылок** на `/tags/...` (страниц тегов для разборов нет).
  `data-pagefind-body` на `<main>` — чтобы разборы искались. Без `ShareLinks`/`EditPost`/`AdjacentPostNav`
  можно обойтись (на усмотрение, но без ошибок сборки). OG-картинки для разборов **не генерировать**
  (никакого `index.png.ts` в `reviews`).
- Главная, `/posts`, `/archives`, `/tags`, `rss.xml.ts`, `getSortedPosts` — **не трогать**: разборы туда
  не попадают уже потому, что они в другой коллекции.

### 4. Меню и i18n
- `src/components/Header.astro`: пункт **«Разборы»** (решение владельца 07.10, при английских соседях) → `/reviews` между «Posts» и «Tags», подсветка активного
  (`isActive("/reviews")`), как у соседних.
- `src/i18n/types.ts` + `src/i18n/lang/en.ts`: ключи `nav.reviews` («Разборы»), заголовок и описание страницы
  списка (`pages.reviewsTitle` «Разборы», `pages.reviewsDesc` «Разборы статей из AI-радара.»).
  Остальной интерфейс сайта английский — его не переводить; подписи типов и контент — русские.

### 5. Стили дайджеста — контракт разметки
News-radar (шаг 2) будет генерировать md-пост дайджеста с HTML-блоками ниже. Задача этого шага —
**стилизовать** их (Tailwind `@apply` / CSS в `src/styles/`, новый файл, например `radar.css`,
подключённый как остальные), в светлой и тёмной теме, на мобильном. Существующие стили не ломать.

```html
<div class="radar-meta">Отобрано 8 из 143 · 3 инструмента · 3 практики · 2 исследования</div>

<div class="radar-lead">
<p class="radar-lead-title">Главное за день</p>
<p>Агенты учатся сами чинить промпты; …</p>
</div>

<h2 class="radar-section">Инструменты</h2>

<article class="radar-card">
<div class="radar-card-head">
<h3 class="radar-card-title" style="view-transition-name: review-2026-10-07-claude-code-fonovye-agenty-512">Claude Code получил фоновых агентов</h3>
<span class="radar-score" data-high="true">8/10</span>
</div>
<p class="radar-takeaway">Вывод: долгие задачи можно отдать в фон.</p>
<p class="radar-summary">Как запускать, где смотреть логи, ограничения по лимитам.</p>
<div class="radar-links">
<a class="radar-btn" href="/reviews/2026-10-07-claude-code-fonovye-agenty-512">Разбор</a>
<a class="radar-btn radar-btn-ext" href="https://…" target="_blank" rel="noopener">Источник</a>
</div>
</article>

<details class="radar-candidates">
<summary>Все кандидаты выпуска (143)</summary>
<ol>
<li><a href="https://…" target="_blank" rel="noopener">Заголовок</a> <span class="radar-score">6/10</span></li>
</ol>
</details>
```

Дизайн — по принятому макету: карточки с тонкой рамкой и скруглением, бейдж оценки справа от заголовка
(цвет по порогу: ≥ 8 — акцентный, 6–7 — нейтральный; признак — атрибут `data-high="true"`, его пишет news-radar, без клиентского скрипта), «Главное за день» — блок с акцентной полосой слева,
кнопки «Разбор»/«Источник» — небольшие контурные. Цвета — из переменных темы блога
(`src/styles/theme.css`), не хардкод. Карточка у разборов в списке `/reviews` — в том же визуальном языке.

### 6. Черновики-образцы (для проверки в dev)
- `blog/src/content/posts/_digests/2026-10-07-ai-radar-sample.md` — дайджест по контракту выше
  (3 раздела, 4–5 карточек, блок кандидатов), `draft: true`, теги `["дайджест", "ai-радар"]`.
- 2–3 разбора в `blog/src/content/reviews/2026/10/…-sample-….md`, `draft: true`, разные `content_type`,
  тело — разделы `## Коротко`, `## Главное`, `## Что взять себе` (как у news-radar), slug совпадает
  со ссылками в образце дайджеста — чтобы переход «карточка → разбор» с анимацией можно было проверить.

`draft: true` — в прод они не попадут; в dev (`astro dev`) видны.

## Ограничения
- **Новых npm-зависимостей нет.** Если без пакета не обойтись — остановиться и написать, какой и зачем.
- Не менять: `Card.astro`, `Layout.astro`, `PostLayout.astro`, страницы и утилиты постов, RSS, `astro.config.ts`,
  `astro-paper.config.ts`, workflows, существующие посты.
- Комментарии в коде — на английском.
- `astro check` должен проходить без ошибок (он часть `npm run build`).
- Ты не можешь запускать docker и писать в `.git` — сборку и коммиты делает Claude Code.

## Критерии приёмки (проверяет Claude Code в `node:22-alpine`)
1. `npm run build` в `blog/` — зелёный (включая `astro check` и pagefind).
2. В `dist/` нет страниц образцов (они `draft`); с временно снятым `draft` у образцов:
   - `/reviews/` показывает разборы, фильтр по типу работает; `/reviews/<slug>/` открывается;
   - разборов **нет** в `dist/index.html`, `dist/posts/`, `dist/rss.xml`, `dist/archives/`, `dist/tags/`;
   - дайджест-образец есть в `/posts/` и `rss.xml`, адрес `/posts/2026-10-07-ai-radar-sample/`;
   - у карточки дайджеста и у заголовка разбора одинаковый `view-transition-name`;
   - разбор находится в индексе pagefind (`dist/pagefind/`).
3. Существующие посты и страницы выглядят как раньше.

## Ответ
Кратко: список изменённых/новых файлов, выбранный способ фильтра по типу, что не получилось или
требует решения.
