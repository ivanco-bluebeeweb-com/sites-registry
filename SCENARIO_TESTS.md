# Scenario Tests (PST) — Sites Registry

Метод: `Docs/session-notes/SCENARIO_TESTING_STANDARD.md`.

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
