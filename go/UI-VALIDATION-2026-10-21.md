# Локальный интерфейс Go-стенда

Дата: 2026-10-21. Windows / Antigravity 2.24.0 / Go SDK 0.1.0.

Go HTTP server + embedded HTML/CSS/JS, без npm runtime и внешних ресурсов.
API работает на loopback, с проверкой Origin/Host и токеном сессии.
Каждый запрос сохраняет отдельный JSON-отчёт. Browser polling отдаёт только
данные для отображения; полный trajectory доступен в отчёте.

`go test -race ./... -count=1` и `go vet ./...` прошли. Добавлены 5 серверных
проверок: вложение и результат, cancel/conflict/recovery, невалидный ввод,
same-origin/token, provider failure и fresh catalog. В общей SDK suite остаётся
1 SKIP symlink-теста из-за прав Windows.

Через реальный браузер проверены:

- Загрузка каталога и выбор `gemini-3.8-flash-low` / `gemini-3-flash`.
- Текстовый ответ и media upload с предпросмотром.
- Vision: все четыре цвета тестовой PNG названы правильно.
- Отмена длительного запроса; UI дождался `cancelled`.
- Следующий запрос на другой модели успешно завершился.
- Детали с native usage и скачивание JSON-отчёта.
- Ширина 390 px: document.scrollWidth = viewport = 390; кнопки высотой 48 px.

Raw evidence: `reports/go-live-20261021-175911.566470900/` и
`reports/go-live-20261021-180406.166462800/`.
Файлы отчётов локальные и исключены из Git. Native quota/availability не гарантируются;
отменённый usage остаётся UNKNOWN. Перезапуск сервера не восстанавливает in-memory
последний запрос; сохранённые JSON остаются на диске.
