# Налаштування GitHub Actions + cron-job.org

Ця інструкція описує, як підключити автоматичний щоденний запуск (1 раз на добу о **07:30** за Києвом) через **cron-job.org** → **GitHub Actions**.

---

## Крок 1. Додати `VISICOM_API_KEY` у GitHub Secrets

1. Відкрийте репозиторій: https://github.com/realtomchuk-source/AdresBazaPidcherg-SvitloSk
2. Перейдіть у **Settings → Secrets and variables → Actions → New repository secret**.
3. Вкажіть:
   - **Name:** `VISICOM_API_KEY`
   - **Secret:** ваш ключ із локального `.env`.
4. Натисніть **Add secret**.

---

## Крок 2. Створити GitHub Personal Access Token (для `cron-job.org`)

1. Перейдіть у GitHub → **Settings (профілю) → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Вкажіть:
   - **Token name:** `cron-job-svitlosk`
   - **Repository access:** Only select repositories → `AdresBazaPidcherg-SvitloSk`
   - **Permissions → Repository permissions → Actions:** `Read and write`
3. Натисніть **Generate token** і скопіюйте токен (`github_pat_...`).

---

## Крок 3. Налаштувати завдання на `cron-job.org`

1. Увійдіть на https://cron-job.org/ і натисніть **Create cronjob**.
2. Заповніть основні поля:
   - **Title:** `SvitloSk Address Monitor`
   - **URL:**
     ```
     https://api.github.com/repos/realtomchuk-source/AdresBazaPidcherg-SvitloSk/actions/workflows/monitor.yml/dispatches
     ```
   - **Execution schedule:** Every day at **07:30** (Timezone: `Europe/Kyiv`).
3. Перейдіть у вкладку **Advanced**:
   - **Request method:** `POST`
   - **Headers** (додайте 3 заголовки):
     - `Accept`: `application/vnd.github+json`
     - `Authorization`: `Bearer github_pat_ВАШ_ТОКЕН`
     - `X-GitHub-Api-Version`: `2022-11-28`
   - **Request body:**
     ```json
     {"ref":"main"}
     ```
4. Збережіть і натисніть **Test run** — у вкладці **Actions** на GitHub має одразу запуститися `SvitloSk Address Monitor`.

---

## Як працює автоматика
- Якщо файл обленерго **не змінився** (`exit code 10`) — workflow завершується зеленим без зайвих комітів.
- Якщо файл **оновився** (`exit code 0`) — workflow автоматично комітить оновлені JSON-файли (`output/addresses.json`, `output/subqueue_changes.json`, `data/state/*`).
- Якщо спрацював захист (`exit code 2`, наприклад битий файл або масова зміна >30% громади) — workflow зупиняється без коміту, а GitHub надсилає вам email-сповіщення.
