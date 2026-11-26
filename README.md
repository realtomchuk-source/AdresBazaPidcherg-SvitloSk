# AdresBazaPidcherg — SvitloSk

[![SvitloSk Address Monitor](https://github.com/realtomchuk-source/AdresBazaPidcherg-SvitloSk/actions/workflows/monitor.yml/badge.svg)](https://github.com/realtomchuk-source/AdresBazaPidcherg-SvitloSk/actions/workflows/monitor.yml)

Автоматизований механізм формування й щоденного моніторингу еталонної бази адрес і підчерг відключень електроенергії (`1.1`–`6.2`) для **Старокостянтинівської міської територіальної громади** (Хмельницька область) — для майбутньої PWA **SvitloSk**.

- **Онлайн-панель моніторингу (GitHub Pages):** https://realtomchuk-source.github.io/AdresBazaPidcherg-SvitloSk/output/admin_report.html
- **Головний чистий JSON для PWA:** [`output/addresses.json`](output/addresses.json)

---

## 🎯 Основні можливості

- **Першоджерело:** офіційні XLSX-переліки Старокостянтинівського РЕМ АТ «Хмельницькобленерго» (`hoe.com.ua`).
- **Подвійна геоверифікація:** OpenStreetMap (Overpass API за `osm_relation: 12272111` + КАТОТТГ усіх 67 населених пунктів) та **Visicom Data API** з геофільтром ≤ 12 км для захисту від однойменних сіл.
- **Класифікатор номерів будинків:** автоматична нормалізація (`21-А → 21а`, `20/а → 20а`, `5\4 → 5/4`), відсіювання опор ЛЕП (`оп.`), кілометрів доріг (`км`) і пікетів, без хибного розгортання діапазонів.
- **Історія змін підчерг:** поадресний diff (`data/state/history.jsonl`), виявлення масових змін по селах (`mass_change`), захист від разового зникнення адрес (2 прогони) та миттєве ручне виключення (`rules/hromada/exclude.json`).
- **Захист від збоїв (Guards) і JSON Schema:** перевірка сигнатури XLSX, заголовків, кількості сіл і записів перед оновленням; валідація фінального `output/addresses.json` за [`schema/addresses.v2.json`](schema/addresses.v2.json).
- **Повна автономність без БД:** увесь стан і результати зберігаються виключно у простих JSON / JSONL файлах. Щоденний запуск о **07:30** за Києвом через `cron-job.org` → GitHub Actions.

---

## 📁 Вихідні файли (`output/`)

| Файл | Призначення |
|---|---|
| `output/addresses.json` | Чиста компактна база адрес (schema 2) для PWA SvitloSk |
| `output/subqueue_changes.json` | Зміни підчерг за останній запуск |
| `output/excluded_log.json` | Журнал виключених об'єктів (опори, км, ручні винятки) |
| `output/needs_review.json` | Черга перевірки спірних записів |
| `output/run_report.json` | Звіт останнього запуску (статус, лічильники, guards) |
| `output/admin_report.html` | Автономна HTML-панель моніторингу (3 вкладки) |

---

## 📚 Документація

- [ARCHITECTURE.md](ARCHITECTURE.md) — архітектура, профіль громади (schema 3) і структура даних
- [MONITORING_PLAN.md](MONITORING_PLAN.md) — логіка моніторингу, коди виходу та захисти
- [GITHUB_SETUP.md](GITHUB_SETUP.md) — налаштування GitHub Actions + `cron-job.org`
- [VISICOM_GUIDE.md](VISICOM_GUIDE.md) — гайд по роботі з Visicom Data API

---

## 🚀 Локальний запуск (Windows)

```powershell
# Повний запуск (з перевіркою hoe.com.ua, OSM і Visicom)
.\.venv\Scripts\python.exe src\monitor.py --profile profiles\hromada.json

# Перезбірка з локального кешу без мережевих запитів
.\.venv\Scripts\python.exe src\monitor.py --skip-fetch --force-rebuild

# Автотести (72 тести)
.\.venv\Scripts\python.exe -m pytest -q
```
