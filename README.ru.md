# Antigravity Engine SDK

**Python и Go SDK для установленного нативного движка Antigravity — с общим браузерным стендом.**

[English](README.md) · [Документация](docs/README.md) · [Релизы](https://github.com/Lotargo/antigravity_engine_sdk/releases)

SDK используют существующий вход в Antigravity, самостоятельно запускают свой
`language_server` и общаются с ним по локальному Connect JSON RPC. Запуск Desktop
и agy CLI не требуется. Python и Go работают независимо друг от друга.

| Компонент | Версия |
| --- | --- |
| Python SDK, Python 3.10+ | **0.2.0** |
| Go SDK, Go 1.23+ | **0.5.0** |
| Проверенный нативный движок | **Antigravity 2.24.0, Windows** |

## Возможности

- Свежий каталог моделей, текст и object JSON Schema через настоящий `finish`.
- Vision, генерация изображений, reasoning, прогресс, отмена и дедлайны.
- Отдельный режим `complete()` / `Complete()` для буквального текста без tools.
- Независимые Supercomplete и Tab-to-Jump с сохранением native diff и позиции.
- Общий стенд с выбором Python / Go, загрузкой кода, подсветкой, номерами строк,
  preview правки, переходом курсора и скачиванием изменённого файла.

![Общий стенд: выбор SDK, модель, изображение и настоящий ответ](docs/assets/chat-vision.jpg)

## Запустить стенд

Нужны установленный Antigravity и выполненный вход в аккаунт. Из checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[dev]"
go -C go build -o testbench/bin/antigravity-testbench-shared.exe ./testbench
.\.venv\Scripts\python.exe python/testbench/server.py
```

Откройте [редактор](http://127.0.0.1:8773/editor) или [чат](http://127.0.0.1:8773/).
Если порт занят, добавьте `--listen 127.0.0.1:8774`.

В [релизе Python](https://github.com/Lotargo/antigravity_engine_sdk/releases/tag/v0.2.0)
также есть ZIP общего стенда для Windows с готовым Go backend. Для него не нужны
Go и Node.js; Python и установленный Antigravity по-прежнему необходимы.

При переключении SDK код, курсор, промпт и вложение сохраняются. Сессии и
последние результаты каждого SDK раздельны; рядом с результатом указаны версия
и время. Сначала запомните baseline в каждом SDK. Во время запроса переключение
заблокировано до завершения или отмены.

## Документация

| Задача | Руководство |
| --- | --- |
| Установка из исходников и релизов | [Getting started](docs/GETTING_STARTED.md) |
| Python API и примеры | [Python SDK](docs/PYTHON.md) |
| Go API и примеры | [Go SDK](go/README.md) |
| Общий интерфейс и сравнение SDK | [Testbench](docs/TESTBENCH.md) |
| Жизненный цикл и происхождение | [Архитектура](docs/ARCHITECTURE.md) |
| Реальные проверки и ограничения | [Validation records](docs/README.md#validation-records) |

Это независимый проект, использующий внутренний протокол движка. Native работа
проверена на Windows; Linux/macOS пока не прошли native acceptance. Ручная
подготовка редактора ограничена проверенной версией 2.24.0.

Код предлагается на проверку, не применяется автоматически. Пустые, отфильтрованные
или неверные подсказки сохраняются буквально. Неизвестный usage не заменяется нулём.

Лицензия нашего SDK пока не выбрана. [Уведомления сторонних зависимостей](THIRD_PARTY.md)
сохранены; движок Antigravity и данные аккаунта не входят в поставку.
