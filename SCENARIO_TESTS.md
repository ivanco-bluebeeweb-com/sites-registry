# Scenario Tests (PST) — Sites Registry

Метод: `Docs/session-notes/SCENARIO_TESTING_STANDARD.md`.

---

## Прогон 2026-08-20 — Часть D (Deploy Verification / Idempotency / Security-SSRF / Regression grep)

**D1 (Deploy Verification):** не применялось — код приложения не менялся (только тесты), деплой не требуется.

**D2 (Idempotency):** добавлен 1 тест. `remove_site` уже проверяет существование записи (`storage.find_by_id`) перед удалением — второй вызов подряд получает чистую ошибку `SITE_NOT_FOUND`, не падает.

**D3 (Security/SSRF):** подтверждено (grep-тестом по исходникам `handlers.py`/`storage.py`) полное отсутствие исходящего HTTP в этом приложении — путь `add_site(platform='wordpress')` идёт через внутренний IPC (`ext.expose`) к WordPress Hub, никогда напрямую не фетчит `url`/`app_password` сайта сам. Поле `url` — сохраняемые данные/поле IPC-payload'а, не цель fetch'а этого приложения. Тест — regression trip-wire: если появится прямой исходящий HTTP-вызов, потребуется отдельный SSRF-ревью.

**D4 (Regression grep):** нет новых находок специфичных для этого приложения сверх `Docs/known-bug-patterns.md`.

**Итог:** 28/28 тестов зелёные (было 26). Реальных багов не найдено.

---

## Прогон 2026-08-19

**Результат аудита покрытия:** 9 функций (7 `@chat.function` +
`ping`/`list_connected_sites`/`upsert_site` через `@ext.expose`), 26
существующих тестов в `tests/test_smoke.py`. Первый грубый скан по имени
функции ошибочно показал 3 «непокрытые» (`list_connected_sites`, `ping`,
`upsert_site`) — ложное срабатывание регулярки на префикс `expose_`
(хендлеры называются `expose_ping`, `expose_upsert_site` и т.д.). Ручная
проверка подтвердила: все три реально вызываются в
`tests/test_smoke.py` (`test_expose_ping_returns_ok_true`,
`test_expose_list_connected_sites_returns_registry_rows`, и
`expose_upsert_site` — в 5 местах, включая идемпотентность по домену).

**Вывод:** покрытие уже полное, новых тестов не потребовалось.

### Результат

26/26 тестов зелёные. **Реальных багов в приложении не найдено.**

---
