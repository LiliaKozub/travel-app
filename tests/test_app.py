"""
Тести для Flask travel-планувальника.

Покриття:
  - Модульні тести (unit): хелпер-функції, алгоритм рекомендацій,
    обрахунок бюджету, допоміжна логіка.
  - Інтеграційні тести: API-ендпоінти через Flask test client
    з ізольованою тимчасовою базою даних.
"""

import json
import math
import os
import sys
import pytest

# Додаємо кореневу директорію проєкту до шляху пошуку модулів
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module


# ─────────────────────────────────────────────
# Фікстури
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Кожен тест отримує чисту PostgreSQL-базу (таблиці скидаються та створюються знову)."""
    test_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/wandr_test"
    )
    monkeypatch.setattr(app_module, "DATABASE_URL", test_url)
    # Drop all tables to guarantee a clean slate
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(test_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS reviews, password_resets, saved_routes, users CASCADE")
    conn.close()
    app_module._init_db()


@pytest.fixture
def client():
    """Flask test client із тестовим режимом."""
    app_module.app.config["TESTING"] = True
    app_module.app.config["SECRET_KEY"] = "test-secret-key"
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def auth_client(client):
    """Test client з вже зареєстрованим та залогіненим користувачем."""
    client.post("/api/auth/register", json={
        "name": "Тест Користувач",
        "email": "test@example.com",
        "password": "password123",
    })
    yield client


@pytest.fixture
def saved_route_id(auth_client):
    """Зберігає тестовий маршрут і повертає його ID."""
    route_data = {
        "title": "Тест Рим",
        "destination": "Рим, Італія",
        "duration": "5 днів",
        "days": [],
    }
    auth_client.post("/api/routes/save", json={
        "route": route_data,
        "interests": ["history", "food"],
        "budget_level": "mid",
    })
    resp = auth_client.get("/api/routes/saved")
    routes = resp.get_json()["routes"]
    return routes[0]["id"]


# ═════════════════════════════════════════════
# МОДУЛЬНІ ТЕСТИ
# ═════════════════════════════════════════════

class TestBuildPeopleStr:
    """Тести функції _build_people_str."""

    def test_single_adult(self):
        assert app_module._build_people_str(1, 0) == "1 мандрівник"

    def test_two_adults(self):
        assert app_module._build_people_str(2, 0) == "2 мандрівники"

    def test_five_adults(self):
        assert app_module._build_people_str(5, 0) == "5 мандрівників"

    def test_adults_with_one_child(self):
        result = app_module._build_people_str(2, 1)
        assert "2 мандрівники" in result
        assert "1 дитина" in result

    def test_adults_with_two_children(self):
        result = app_module._build_people_str(3, 2)
        assert "2 дитини" in result

    def test_adults_with_five_children(self):
        result = app_module._build_people_str(1, 5)
        assert "5 дітей" in result

    def test_no_children_no_plus(self):
        result = app_module._build_people_str(2, 0)
        assert "+" not in result


class TestBuildWeatherContext:
    """Тести функції _build_weather_context."""

    def test_empty_inputs_returns_empty(self):
        assert app_module._build_weather_context("", "", "") == ""

    def test_weather_pref_only(self):
        result = app_module._build_weather_context("тепло", "", "")
        assert "тепло" in result

    def test_dates_only(self):
        result = app_module._build_weather_context("", "2025-07-01", "2025-07-07")
        assert "2025-07-01" in result
        assert "2025-07-07" in result

    def test_both_pref_and_dates(self):
        result = app_module._build_weather_context("сонячно", "2025-08-01", "2025-08-10")
        assert "сонячно" in result
        assert "2025-08-01" in result


class TestIsUkraine:
    """Тести функції _is_ukraine."""

    def test_kyiv_latin(self):
        assert app_module._is_ukraine("Kyiv") is True

    def test_kyiv_cyrillic(self):
        assert app_module._is_ukraine("Київ") is True

    def test_lviv(self):
        assert app_module._is_ukraine("Lviv") is True

    def test_ukraine_word(self):
        assert app_module._is_ukraine("Ukraine") is True

    def test_paris_is_not_ukraine(self):
        assert app_module._is_ukraine("Paris") is False

    def test_berlin_is_not_ukraine(self):
        assert app_module._is_ukraine("Berlin") is False

    def test_empty_string(self):
        assert app_module._is_ukraine("") is False


class TestBuildUkraineTransportNote:
    """Тести функції _build_ukraine_transport_note."""

    def test_ukraine_city_returns_note(self):
        note = app_module._build_ukraine_transport_note("Київ")
        assert len(note) > 0
        assert "КРИТИЧНО" in note or "аеропорт" in note.lower()

    def test_foreign_city_returns_empty(self):
        note = app_module._build_ukraine_transport_note("Варшава")
        assert note == ""

    def test_empty_city_returns_empty(self):
        note = app_module._build_ukraine_transport_note("")
        assert note == ""


class TestGetBudgetGuide:
    """Тести функції _get_budget_guide."""

    def test_budget_level(self):
        result = app_module._get_budget_guide("budget")
        assert "БЮДЖЕТНИЙ" in result

    def test_mid_level(self):
        result = app_module._get_budget_guide("mid")
        assert "КОМФОРТ" in result

    def test_premium_level(self):
        result = app_module._get_budget_guide("premium")
        assert "ПРЕМІУМ" in result

    def test_unknown_level_returns_mid(self):
        result = app_module._get_budget_guide("unknown")
        assert "КОМФОРТ" in result


class TestBuildSpecialContext:
    """Тести функції _build_special_context."""

    def test_no_special_conditions(self):
        result = app_module._build_special_context(False, False, 0)
        assert result == ""

    def test_with_children(self):
        result = app_module._build_special_context(True, False, 2)
        assert "ДІТИ" in result or "дітей" in result.lower() or "дітей" in result

    def test_with_accessibility(self):
        result = app_module._build_special_context(False, True, 0)
        assert "ПОТРЕБИ" in result or "обмежен" in result.lower()

    def test_both_children_and_accessibility(self):
        result = app_module._build_special_context(True, True, 1)
        assert len(result) > 0


class TestScorePhoto:
    """Тести функції _score_photo."""

    def test_portrait_photo_penalized(self):
        photo = {"widthPx": 300, "heightPx": 500}  # ratio 0.6 — portrait
        score = app_module._score_photo(photo, 0)
        assert score < 0

    def test_landscape_interior_scores_high(self):
        photo = {"widthPx": 600, "heightPx": 400}  # ratio 1.5 — sweet spot
        score = app_module._score_photo(photo, 0)
        assert score > 0.5

    def test_ultra_wide_penalized(self):
        photo = {"widthPx": 1200, "heightPx": 300}  # ratio 4.0 — ultra wide
        score = app_module._score_photo(photo, 0)
        assert score < 0

    def test_later_index_scores_lower(self):
        photo = {"widthPx": 600, "heightPx": 400}
        score_first = app_module._score_photo(photo, 0)
        score_later = app_module._score_photo(photo, 5)
        assert score_first > score_later

    def test_no_dimensions_returns_small_value(self):
        photo = {}
        score = app_module._score_photo(photo, 0)
        assert isinstance(score, float)


class TestFinalizeBudget:
    """Тести функції _finalize_budget."""

    def test_no_hotels_keeps_original_accommodation(self):
        bd = {"accommodation": {"price_per_night": 80, "nights": 3, "subtotal": 240, "note": "AI"}}
        app_module._finalize_budget(bd, [], 1, 0, 3)
        assert bd["accommodation"]["price_per_night"] == 80

    def test_hotels_override_accommodation(self):
        hotels = [{"price_per_night": 100}, {"price_per_night": 120}]
        bd = {}
        app_module._finalize_budget(bd, hotels, 1, 0, 3)
        assert bd["accommodation"]["price_per_night"] == 110  # avg(100,120)
        assert bd["accommodation"]["subtotal"] == 330

    def test_total_min_max_calculated(self):
        bd = {
            "accommodation": {"subtotal": 300},
            "food":          {"subtotal": 150},
            "transport":     {"subtotal": 100},
            "activities":    {"subtotal": 50},
            "local_transport": {"subtotal": 20},
            "misc":          {"subtotal": 30},
        }
        app_module._finalize_budget(bd, [], 1, 0, 3)
        total = 300 + 150 + 100 + 50 + 20 + 30  # = 650
        assert bd["total_min"] == round(total * 0.9)
        assert bd["total_max"] == round(total * 1.15)

    def test_single_person_no_scaling(self):
        bd = {"food": {"subtotal": 100, "per_day": 20, "days": 5}}
        app_module._finalize_budget(bd, [], 1, 0, 5)
        # 1 person — no scaling
        assert bd["food"]["subtotal"] == 100

    def test_multi_person_scales_food(self):
        bd = {"food": {"subtotal": 100, "per_day": 20, "days": 5}}
        app_module._finalize_budget(bd, [], 2, 0, 5)
        # AI gave single-person amount (100 ≈ 20*5), should scale by 2
        assert bd["food"]["subtotal"] == 200


class TestGetRecommendations:
    """Тести алгоритму рекомендацій get_recommendations."""

    def _insert_user_and_route(self, email, interests, budget_level):
        from werkzeug.security import generate_password_hash
        with app_module._get_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO users (email, name, password_hash) VALUES (?,?,?)",
                (email, "Test", generate_password_hash("pass"))
            )
            db.commit()
            uid = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
            db.execute(
                "INSERT INTO saved_routes (user_id, title, destination, duration, route_data, interests, budget_level)"
                " VALUES (?,?,?,?,?,?,?)",
                (uid, f"Route {email}", "Рим", "3 дні", "{}", json.dumps(interests), budget_level)
            )
            db.commit()
            rid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return uid, rid

    def test_returns_list(self):
        result = app_module.get_recommendations(None, ["food"], "mid")
        assert isinstance(result, list)

    def test_empty_db_returns_empty(self):
        result = app_module.get_recommendations(None, ["food"], "mid")
        assert result == []

    def test_excludes_own_routes(self):
        uid, rid = self._insert_user_and_route("owner@test.com", ["food"], "mid")
        result = app_module.get_recommendations(uid, ["food"], "mid")
        ids = [r["id"] for r in result]
        assert rid not in ids

    def test_includes_other_users_routes(self):
        uid, rid = self._insert_user_and_route("other@test.com", ["food"], "mid")
        result = app_module.get_recommendations(999, ["food"], "mid")
        ids = [r["id"] for r in result]
        assert rid in ids

    def test_interest_match_scores_higher(self):
        self._insert_user_and_route("match@test.com", ["food", "culture"], "mid")
        self._insert_user_and_route("nomatch@test.com", ["hiking", "adventure"], "mid")
        result = app_module.get_recommendations(None, ["food", "culture"], "mid")
        assert len(result) >= 2
        assert result[0]["score"] >= result[1]["score"]

    def test_budget_match_preferred(self):
        self._insert_user_and_route("budget_match@test.com", ["food"], "mid")
        self._insert_user_and_route("budget_miss@test.com", ["food"], "premium")
        result = app_module.get_recommendations(None, ["food"], "mid")
        assert len(result) >= 1

    def test_deduplication_by_title(self):
        from werkzeug.security import generate_password_hash
        with app_module._get_db() as db:
            for i in range(3):
                db.execute(
                    "INSERT OR IGNORE INTO users (email, name, password_hash) VALUES (?,?,?)",
                    (f"dup{i}@test.com", "U", generate_password_hash("p"))
                )
            db.commit()
            user_ids = [db.execute("SELECT id FROM users WHERE email=?", (f"dup{i}@test.com",)).fetchone()[0] for i in range(3)]
            for uid in user_ids:
                db.execute(
                    "INSERT INTO saved_routes (user_id, title, destination, duration, route_data, interests, budget_level)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (uid, "Same Title Route", "Рим", "3 дні", "{}", '["food"]', "mid")
                )
            db.commit()
        result = app_module.get_recommendations(None, ["food"], "mid")
        titles = [r["title"] for r in result]
        assert len(titles) == len(set(titles))

    def test_limit_respected(self):
        from werkzeug.security import generate_password_hash
        with app_module._get_db() as db:
            for i in range(10):
                db.execute(
                    "INSERT OR IGNORE INTO users (email, name, password_hash) VALUES (?,?,?)",
                    (f"u{i}@t.com", "U", generate_password_hash("p"))
                )
            db.commit()
            for i in range(10):
                uid = db.execute("SELECT id FROM users WHERE email=?", (f"u{i}@t.com",)).fetchone()[0]
                db.execute(
                    "INSERT INTO saved_routes (user_id, title, destination, duration, route_data, interests, budget_level)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (uid, f"Route {i}", "Місто", "3 дні", "{}", '["food"]', "mid")
                )
            db.commit()
        result = app_module.get_recommendations(None, ["food"], "mid", limit=3)
        assert len(result) <= 3


# ═════════════════════════════════════════════
# ІНТЕГРАЦІЙНІ ТЕСТИ
# ═════════════════════════════════════════════

class TestIndexPage:
    """Тест головної сторінки."""

    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert b"<!DOCTYPE html>" in resp.data or b"<html" in resp.data


class TestAuthRegister:
    """Інтеграційні тести реєстрації."""

    def test_register_success(self, client):
        resp = client.post("/api/auth/register", json={
            "name": "Іван", "email": "ivan@test.com", "password": "password123"
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["user"]["email"] == "ivan@test.com"

    def test_register_missing_fields(self, client):
        resp = client.post("/api/auth/register", json={"email": "x@x.com"})
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_register_short_password(self, client):
        resp = client.post("/api/auth/register", json={
            "name": "X", "email": "x@test.com", "password": "123"
        })
        assert resp.status_code == 400

    def test_register_duplicate_email(self, client):
        payload = {"name": "X", "email": "dup@test.com", "password": "password123"}
        client.post("/api/auth/register", json=payload)
        resp = client.post("/api/auth/register", json=payload)
        assert resp.status_code == 409


class TestAuthLogin:
    """Інтеграційні тести входу."""

    def test_login_success(self, client):
        client.post("/api/auth/register", json={
            "name": "Оля", "email": "olya@test.com", "password": "mypassword1"
        })
        resp = client.post("/api/auth/login", json={
            "email": "olya@test.com", "password": "mypassword1"
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True

    def test_login_wrong_password(self, client):
        client.post("/api/auth/register", json={
            "name": "X", "email": "x@test.com", "password": "correct123"
        })
        resp = client.post("/api/auth/login", json={
            "email": "x@test.com", "password": "wrong"
        })
        assert resp.status_code == 401

    def test_login_unknown_email(self, client):
        resp = client.post("/api/auth/login", json={
            "email": "nobody@test.com", "password": "any"
        })
        assert resp.status_code == 401


class TestAuthMe:
    """Тести ендпоінту /api/auth/me."""

    def test_me_unauthenticated(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.get_json()["user"] is None

    def test_me_authenticated(self, auth_client):
        resp = auth_client.get("/api/auth/me")
        data = resp.get_json()
        assert data["user"] is not None
        assert data["user"]["email"] == "test@example.com"


class TestAuthLogout:
    """Тест виходу."""

    def test_logout_clears_session(self, auth_client):
        auth_client.post("/api/auth/logout")
        resp = auth_client.get("/api/auth/me")
        assert resp.get_json()["user"] is None


class TestSaveRoutes:
    """Інтеграційні тести збереження маршрутів."""

    def test_save_requires_auth(self, client):
        resp = client.post("/api/routes/save", json={
            "route": {"title": "Test"}, "interests": [], "budget_level": "mid"
        })
        assert resp.status_code == 401

    def test_save_success(self, auth_client):
        resp = auth_client.post("/api/routes/save", json={
            "route": {"title": "Рим", "destination": "Рим, Італія", "duration": "5 днів"},
            "interests": ["history"],
            "budget_level": "mid",
        })
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_saved_routes_listed(self, auth_client):
        auth_client.post("/api/routes/save", json={
            "route": {"title": "Маршрут 1"}, "interests": [], "budget_level": "budget"
        })
        resp = auth_client.get("/api/routes/saved")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["routes"]) == 1
        assert data["routes"][0]["title"] == "Маршрут 1"

    def test_saved_routes_requires_auth(self, client):
        resp = client.get("/api/routes/saved")
        assert resp.status_code == 401

    def test_get_saved_route_by_id(self, auth_client, saved_route_id):
        resp = auth_client.get(f"/api/routes/saved/{saved_route_id}")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_get_other_users_route_returns_404(self, auth_client, saved_route_id):
        # Реєструємо другого користувача
        auth_client.post("/api/auth/logout")
        auth_client.post("/api/auth/register", json={
            "name": "Інший", "email": "other@test.com", "password": "other12345"
        })
        resp = auth_client.get(f"/api/routes/saved/{saved_route_id}")
        assert resp.status_code == 404

    def test_delete_route(self, auth_client, saved_route_id):
        resp = auth_client.delete(f"/api/routes/saved/{saved_route_id}")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_delete_requires_auth(self, client):
        resp = client.delete("/api/routes/saved/1")
        assert resp.status_code == 401


class TestRoutesPreview:
    """Тести публічного ендпоінту перегляду маршруту."""

    def test_preview_existing_route(self, auth_client, saved_route_id):
        resp = auth_client.get(f"/api/routes/preview/{saved_route_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "title" in data

    def test_preview_nonexistent_route(self, client):
        resp = client.get("/api/routes/preview/99999")
        assert resp.status_code == 404

    def test_preview_is_public(self, client, auth_client, saved_route_id):
        # Навіть без логіну preview доступний
        resp = client.get(f"/api/routes/preview/{saved_route_id}")
        assert resp.status_code == 200


class TestReviews:
    """Інтеграційні тести відгуків."""

    def test_get_reviews_empty(self, client, auth_client, saved_route_id):
        resp = client.get(f"/api/reviews/{saved_route_id}")
        data = resp.get_json()
        assert data["success"] is True
        assert data["reviews"] == []
        assert data["total"] == 0

    def test_add_review_requires_auth(self, auth_client, saved_route_id):
        auth_client.post("/api/auth/logout")
        resp = auth_client.post("/api/reviews/add", json={
            "route_id": saved_route_id, "rating": 5, "comment": "Чудово!"
        })
        assert resp.status_code == 401

    def test_add_review_success(self, auth_client, saved_route_id):
        resp = auth_client.post("/api/reviews/add", json={
            "route_id": saved_route_id, "rating": 4, "comment": "Дуже гарний маршрут"
        })
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_review_appears_in_get(self, auth_client, saved_route_id):
        auth_client.post("/api/reviews/add", json={
            "route_id": saved_route_id, "rating": 5, "comment": "Відмінно!"
        })
        resp = auth_client.get(f"/api/reviews/{saved_route_id}")
        data = resp.get_json()
        assert data["total"] == 1
        assert data["reviews"][0]["rating"] == 5
        assert data["reviews"][0]["comment"] == "Відмінно!"

    def test_add_review_invalid_rating(self, auth_client, saved_route_id):
        resp = auth_client.post("/api/reviews/add", json={
            "route_id": saved_route_id, "rating": 10
        })
        assert resp.status_code == 400

    def test_add_review_missing_rating(self, auth_client, saved_route_id):
        resp = auth_client.post("/api/reviews/add", json={
            "route_id": saved_route_id
        })
        assert resp.status_code == 400

    def test_review_replace_existing(self, auth_client, saved_route_id):
        auth_client.post("/api/reviews/add", json={
            "route_id": saved_route_id, "rating": 3, "comment": "Непогано"
        })
        auth_client.post("/api/reviews/add", json={
            "route_id": saved_route_id, "rating": 5, "comment": "Переглянув — відмінно!"
        })
        resp = auth_client.get(f"/api/reviews/{saved_route_id}")
        data = resp.get_json()
        # INSERT OR REPLACE — залишається лише один відгук від цього користувача
        assert data["total"] == 1
        assert data["reviews"][0]["rating"] == 5

    def test_avg_rating_calculated(self, auth_client, saved_route_id):
        auth_client.post("/api/reviews/add", json={
            "route_id": saved_route_id, "rating": 4, "comment": "Добре"
        })
        resp = auth_client.get(f"/api/reviews/{saved_route_id}")
        data = resp.get_json()
        assert data["avg_rating"] == 4.0


class TestSimilarRoutes:
    """Тести ендпоінту схожих маршрутів."""

    def test_similar_routes_returns_list(self, client):
        resp = client.post("/api/routes/similar", json={
            "interests": ["food", "history"],
            "budget_level": "mid",
        })
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert isinstance(data["routes"], list)

    def test_similar_routes_empty_db(self, client):
        resp = client.post("/api/routes/similar", json={
            "interests": ["food"], "budget_level": "mid"
        })
        assert resp.get_json()["routes"] == []

    def test_similar_routes_with_data(self, auth_client):
        auth_client.post("/api/routes/save", json={
            "route": {"title": "Рим", "destination": "Рим, Італія", "duration": "4 дні"},
            "interests": ["food", "history"],
            "budget_level": "mid",
        })
        # Логінуємось як інший користувач, щоб бачити маршрути першого
        auth_client.post("/api/auth/logout")
        auth_client.post("/api/auth/register", json={
            "name": "Інший", "email": "other2@test.com", "password": "other12345"
        })
        resp = auth_client.post("/api/routes/similar", json={
            "interests": ["food", "history"], "budget_level": "mid"
        })
        data = resp.get_json()
        assert len(data["routes"]) >= 1


class TestForgotPassword:
    """Тести відновлення пароля."""

    def test_forgot_password_existing_email(self, client):
        client.post("/api/auth/register", json={
            "name": "X", "email": "reset@test.com", "password": "password123"
        })
        resp = client.post("/api/auth/forgot-password", json={"email": "reset@test.com"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True

    def test_forgot_password_unknown_email(self, client):
        # Не розкриває, чи існує email
        resp = client.post("/api/auth/forgot-password", json={"email": "nobody@test.com"})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_forgot_password_missing_email(self, client):
        resp = client.post("/api/auth/forgot-password", json={})
        assert resp.status_code == 400
