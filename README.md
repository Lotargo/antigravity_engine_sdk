# Antigravity Engine SDK

Python SDK для прямого подключения к установленному нативному движку Antigravity 2.x.
Извлечён из рабочего адаптера VEIL; исходный проект остаётся независимым.

SDK запускает `language_server` самостоятельно, использует существующий вход
Antigravity через системное хранилище и общается с процессом через локальный
Connect JSON RPC. Работает без запуска Desktop и без agy CLI.

## Установка

Python 3.10+, установленный Antigravity 2.x и выполненный хотя бы один раз вход
в Antigravity. SDK не распространяет сам движок.

```powershell
python -m pip install "F:\projects\antigravity_engine_sdk"
```

Для разработки:

```powershell
cd F:\projects\antigravity_engine_sdk
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

Дополнительная runtime-зависимость — `jsonschema` для локальной проверки результата.
Flask, Pydantic, ComfyUI, файлы VEIL и установленный agy CLI не требуются.

## Текст и каталог моделей

```python
from antigravity_engine_sdk import Client

with Client() as client:
    models = client.models()  # Не вызывает генерацию.
    for model in models:
        print(model.id, model.name, model.supports_images)

    # Выберите точный ID из каталога вашего аккаунта.
    selected_model = "gemini-3.8-flash-low"
    result = client.chat("Объясни принцип работы объектива в двух предложениях.", model=selected_model)
    print(result.text)
    print(result.usage)
```

Импорт и вход в контекст не запускают процесс; первый запрос запускает движок.
Один `Client` переиспользует свой процесс до `close()` или выхода из `with`.
Разные клиенты имеют отдельные процессы. Вызовы одного клиента выполняются
последовательно; каждый high-level запрос получает новую беседу и временную
рабочую папку. История между `chat()` не переносится автоматически.

## Структурированный ответ и vision

```python
from antigravity_engine_sdk import Client, Media

schema = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "colors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["description", "colors"],
    "additionalProperties": False,
}

with Client() as client:
    result = client.generate(
        "Опиши изображение и перечисли основные цвета.",
        model="gemini-3.8-flash-low",
        schema=schema,
        media=[Media.from_file("photo.png")],
        instructions="Будь краток. Отделяй наблюдения от предположений.",
    )
    print(result.output)
```

Принимается object JSON Schema, включая локальные `$defs`/`$ref`; внешние ссылки
не поддерживаются. Ответ обязательно приходит через нативный `finish`, после
чего SDK повторно проверяет JSON и схему локально. Текст планировщика не
подменяет результат. `chat()` использует тот же механизм с полем `text`.

`media` принимает несколько `Media` с байтами и MIME type, до 20 MiB на элемент.
Поддержка конкретных форматов зависит от модели; `supports_images` берётся из
нативного каталога. SDK проверяет наличие media в возвращённом user-input шаге.

## Reasoning, прогресс и отмена

```python
from threading import Event
from antigravity_engine_sdk import AntigravityError, Client

stop = Event()  # В обработчике кнопки Stop вызовите stop.set().

with Client(timeout=180) as client:
    try:
        result = client.chat(
            "Реши задачу и объясни ответ: 17 * 23.",
            model="gemini-3-flash",  # Точный ID из вашего каталога.
            thinking="high",
            cancelled=stop.is_set,
            on_progress=lambda event: print(event.phase),
        )
        print(result.text)
    except AntigravityError as error:
        print(error.code, str(error))
```

`thinking` — `minimal`, `low`, `medium`, `high`; отсутствие значения сохраняет
нативные настройки. Каталожные варианты `*-low/medium/high` используются как
точные IDs. Поддержка override зависит от модели: у Claude предпочтительны
каталожные варианты, GPT-OSS в проверенном движке не принимал `minimal`.
SDK не придумывает и не пересобирает идентификаторы моделей.

`on_progress` получает `ProgressEvent` с фазами `starting`, `working`, `trajectory`,
`completed`. `trajectory` содержит снимок нативных шагов, включая доступный текст
и метаданные. Это polling, а не поток токенов. Callback выполняется синхронно;
не вызывайте этот же клиент из callback.

Срок запроса включает ожидание очереди, запуск и RPC. Отмена отправляет
`CancelCascadeInvocation`. Если остановку нельзя подтвердить успешным RPC,
SDK закрывает принадлежащий клиенту процесс. После обычной отмены клиент можно
использовать снова. `close()` терминален и останавливает активную работу клиента.

## Генерация изображений

```python
from antigravity_engine_sdk import Client

with Client(timeout=180) as client:
    result = client.generate_image(
        "A blue ceramic cup with a yellow triangle on a white table. One square image.",
        planner="gemini-3.8-flash-low",
        expected_model="gemini-3.1-flash-image",
    )
    result.images[0].save("cup.jpg")  # Расширение выбирайте по artifact.mime_type.
```

Здесь `planner` — текстовая модель, которая вызывает нативный `generate_image`.
`expected_model` проверяет модель, указанную в ответе инструмента; это не параметр
принудительного выбора image-модели. У проверенного 2.23.1 инструмент не
предоставлял такого параметра. SDK требует настоящий image step и байты
изображения, сохраняет байты в результате до удаления временных файлов и
никогда не перезаписывает существующий файл через `ImageArtifact.save()`.
Внешние URI результата не скачиваются и не читаются как локальные файлы.

Reference media можно передать через `media`; точность редактирования,
multi-view и доступность других image-моделей требуют отдельной проверки.

## Доступ к движку без ограничения high-level API

```python
from antigravity_engine_sdk import Client, Engine, find_installation

with Client() as client:
    native = client.rpc("GetAvailableModels")

installation = find_installation()
if installation:
    with Engine(installation, timeout=60) as engine:
        response = engine.rpc("GetAvailableModels")
```

`Client.rpc()` даёт доступ к внутренним методам движка. Для собственных бесед,
инструментов, продолжения cascade и иных возможностей используйте `Engine.rpc()`
и текущую схему протокола. Неподтверждённые поля движок может молча игнорировать.
High-level API разрешает только `finish` либо `generate_image` + `finish`.
Произвольный RPC находится под управлением вызывающего проекта.

Нестандартная установка: `Client(executable=".../resources/bin/language_server.exe")`
или переменная `ANTIGRAVITY_ENGINE_PATH`. Можно передать явный
`Installation(executable, version)` для собственного обнаружения.

## Ошибки и совместимость

`AntigravityError.code` различает `engine_unavailable`, `authentication_required`,
`engine_connection`, `engine_exited`, `timeout`, `cancelled`, `client_closed`,
`invalid_input`, `invalid_schema`, `model_unavailable`, `incompatible_image_input`,
`provider_error`, `content_rejected`, `incompatible_format`, `contract_violation`,
`image_missing`, `image_model_mismatch`. `details` сохраняет диагностический контекст;
результат, trajectory и details могут содержать текст запроса — храните их осознанно.

`Result` содержит output/raw_output и его SHA-256 (`raw_response_sha256`), trajectory,
cascade ID, исходный и нативный ID модели, версию движка, длительность и usage. Usage сохраняет нативные поля,
включая строковые счётчики; область учёта — `trajectory_model_steps`.
Это не общий расход аккаунта. Usage отменённых запросов и цена изображений
неизвестны, SDK не записывает их как нулевые.

В основе лежит внутренний протокол Antigravity, а не официальный публичный Google
SDK. Исходный адаптер проверен на Windows / Antigravity 2.23.1; выделенный SDK
прошёл live-проверку на Antigravity 2.24.0. Пути обнаружения
Linux/macOS сохранены; native acceptance этих платформ отсутствует. После
обновлений движка может потребоваться адаптация. В SDK намеренно нет устаревающего
списка «запрещённых» моделей: каталог не гарантирует доступную inference capacity.

Подробности происхождения и границ: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Результаты проверки этого выделения: [docs/VALIDATION-2026-10-21.md](docs/VALIDATION-2026-10-21.md).

Лицензия сохранена от исходного кода: AGPL-3.0-only, см. [LICENSE](LICENSE).
