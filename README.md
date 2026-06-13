# AI Travel — Персоналізований тревел-планувальник

AI-застосунок для підбору туристичних маршрутів на базі Flask + Claude API.

## Структура проєкту

```
travel-app/
├── app.py              # Flask бекенд + API endpoints
├── requirements.txt    # Залежності
├── templates/
│   └── index.html      # Інтерфейс застосунку
└── README.md
```

## Як запустити

### 1. Встановіть залежності

```bash
pip install -r requirements.txt
```

### 2. Встановіть API ключ Anthropic

```bash
# Linux / macOS
export ANTHROPIC_API_KEY="sk-ant-..."

# Windows (PowerShell)
$env:ANTHROPIC_API_KEY="sk-ant-..."
```

Отримати ключ: https://console.anthropic.com/

### 3. Запустіть сервер

```bash
python app.py
```

### 4. Відкрийте браузер

```
http://localhost:5000
```

---

## Як працює

**Комбінований підхід:**
1. **Фільтрація по базі** — маршрути фільтруються за бюджетом та інтересами з вбудованої бази (8 маршрутів).
2. **AI-генерація** — Claude створює унікальний персоналізований маршрут з урахуванням ВСІХ параметрів.
3. **Результат** — показується AI-маршрут + схожі маршрути з бази.

## API Endpoints

| Метод | URL | Опис |
|-------|-----|------|
| GET | `/` | Головна сторінка |
| POST | `/api/generate-route` | AI-генерація маршруту |
| POST | `/api/quick-routes` | Фільтрація з бази без AI |

### POST /api/generate-route

```json
{
  "budget": 500,
  "interests": ["їжа", "архітектура"],
  "destination": "Італія",
  "duration": "7 днів",
  "extra_notes": "Подорожуємо з дітьми"
}
```

## Розширення

Щоб додати більше маршрутів до бази — розширте масив `ROUTES_DB` у `app.py`. Кожен маршрут має поля:
- `interests` — масив: `їжа`, `архітектура`, `природа`, `культура`, `пригоди`, `пляж`
- `budget_min` / `budget_max` — в EUR
