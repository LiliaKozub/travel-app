import os
import json
import ssl
import time
import hashlib
import secrets
import psycopg2
import psycopg2.extras
import psycopg2.errors
import smtplib
import http.client
import urllib.parse
import urllib.request
import urllib.error
import requests as _requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import threading
from flask import Flask, render_template, request, jsonify, Response, session, stream_with_context
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "wandr-secret-key-change-in-production")

# ── EMAIL CONFIG ──
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM     = os.environ.get("EMAIL_FROM", "onboarding@resend.dev")
APP_URL        = os.environ.get("APP_URL", "http://localhost:5000")
# Legacy SMTP vars kept for backwards compat but unused when RESEND_API_KEY is set
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")

def _send_email(to: str, subject: str, html: str) -> bool:
    """Send an HTML email via Resend API. Returns True on success."""
    if not RESEND_API_KEY:
        return False
    try:
        resp = _requests.post(
            "https://api.resend.com/emails",
            json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html},
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            timeout=10,
        )
        if not resp.ok:
            print(f"[EMAIL ERROR] {resp.status_code}: {resp.text}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {type(e).__name__}: {e}", flush=True)
        return False

# ── DATABASE ──
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/wandr"
)


class _DBWrapper:
    """Wraps psycopg2 connection + RealDictCursor to expose a SQLite-compatible API.

    All route handlers use `?` placeholders (SQLite style); execute() converts
    them to `%s` automatically so the SQL strings need no other changes.
    """

    def __init__(self, conn):
        self._conn = conn
        self._cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql, params=()):
        self._cur.execute(sql.replace("?", "%s"), params or ())
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type:
            self._conn.rollback()
        self._conn.close()


def _get_db():
    """Open a PostgreSQL connection with dict-like row access."""
    return _DBWrapper(psycopg2.connect(DATABASE_URL))

def _init_db():
    """Create all tables if they do not exist."""
    with _get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                interests TEXT DEFAULT '[]',
                created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS saved_routes (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL,
                destination TEXT,
                duration TEXT,
                route_data TEXT NOT NULL,
                interests TEXT DEFAULT '[]',
                budget_level TEXT DEFAULT 'mid',
                created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                route_id INTEGER NOT NULL REFERENCES saved_routes(id),
                rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                comment TEXT,
                created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
                UNIQUE(user_id, route_id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                token TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0
            )
        """)
        # Safe migrations for columns added after initial deploy
        for stmt in [
            "ALTER TABLE saved_routes ADD COLUMN IF NOT EXISTS interests TEXT DEFAULT '[]'",
            "ALTER TABLE saved_routes ADD COLUMN IF NOT EXISTS budget_level TEXT DEFAULT 'mid'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS interests TEXT DEFAULT '[]'",
        ]:
            db.execute(stmt)
        db.commit()

_init_db()

# ── SEED DATA ──

_SEED_USERS = [
    ("olena.kovalenko@ukr.net",        "Олена Коваленко"),
    ("taras.bondarenko@gmail.com",     "Тарас Бондаренко"),
    ("nataliia.kuzmenko@gmail.com",    "Наталія Кузьменко"),
    ("ivan.koval@ukr.net",             "Іван Коваль"),
    ("yuliia.doroshenko@gmail.com",    "Юлія Дорошенко"),
    ("andriy.melnyk@gmail.com",        "Андрій Мельник"),
    ("solomiya.ivanenko@ukr.net",      "Соломія Іваненко"),
    ("dmytro.shevchenko@gmail.com",    "Дмитро Шевченко"),
    ("iryna.petrenko@ukr.net",         "Ірина Петренко"),
    ("maksym.tkachenko@gmail.com",     "Максим Ткаченко"),
    ("kateryna.voloshyn@gmail.com",    "Катерина Волошин"),
    ("oleh.kravchenko@ukr.net",        "Олег Кравченко"),
    ("liudmyla.nazarenko@gmail.com",   "Людмила Назаренко"),
    ("serhiy.boyko@gmail.com",         "Сергій Бойко"),
    ("hanna.serhiyenko@ukr.net",       "Ганна Сергієнко"),
    ("vasyl.polishchuk@gmail.com",     "Василь Поліщук"),
    ("viktoria.danchenko@gmail.com",   "Вікторія Данченко"),
    ("roman.savchenko@ukr.net",        "Роман Савченко"),
    ("tetiana.khomenko@gmail.com",     "Тетяна Хоменко"),
    ("yaroslav.rudenko@gmail.com",     "Ярослав Руденко"),
    ("oksana.kovalchuk@ukr.net",       "Оксана Ковальчук"),
    ("mykola.lysenko@gmail.com",       "Микола Лисенко"),
    ("lesia.romanenko@ukr.net",        "Леся Романенко"),
    ("denys.marchenko@gmail.com",      "Денис Марченко"),
    ("alina.yeremenko@gmail.com",      "Аліна Єременко"),
    ("artem.pavlenko@ukr.net",         "Артем Павленко"),
    ("daryna.stetsiuk@gmail.com",      "Дарина Стецюк"),
    ("yevhen.sydorenko@gmail.com",     "Євген Сидоренко"),
    ("valeriia.honcharenko@ukr.net",   "Валерія Гончаренко"),
    ("oleksiy.hrytsenko@gmail.com",    "Олексій Гриценко"),
    ("maryna.vlasenko@ukr.net",        "Марина Власенко"),
    ("yuriiy.prykhodko@gmail.com",     "Юрій Приходько"),
    ("anastasiia.berezhna@gmail.com",  "Анастасія Бережна"),
    ("bohdan.zakharenko@ukr.net",      "Богдан Захаренко"),
    ("nina.sokol@gmail.com",           "Ніна Сокол"),
    ("vladyslav.nechyporenko@gmail.com","Владислав Нечипоренко"),
    ("nadiia.huk@ukr.net",             "Надія Гук"),
    ("ihor.ostapenko@gmail.com",       "Ігор Остапенко"),
    ("svitlana.shuliak@ukr.net",       "Світлана Шуляк"),
    ("pavlo.bilous@gmail.com",         "Павло Білоус"),
    ("zhanna.reva@ukr.net",            "Жанна Рева"),
    ("stanislav.kolomiyets@gmail.com", "Станіслав Коломієць"),
    ("krystyna.bilyk@ukr.net",         "Крістіна Білик"),
    ("vladyslav.kharchenko@gmail.com", "Владислав Харченко"),
    ("polina.lutsenko@ukr.net",        "Поліна Луценко"),
    ("kostiantyn.ponomarenko@gmail.com","Костянтин Пономаренко"),
    ("khrystyna.ilchenko@ukr.net",     "Христина Ільченко"),
    ("anton.semenenko@gmail.com",      "Антон Семененко"),
    ("myroslava.sahala@ukr.net",       "Мирослава Сагала"),
    ("ivan.diachenko@gmail.com",       "Іван Дяченко"),
    ("halyna.hnatiuk@ukr.net",         "Галина Гнатюк"),
    ("mykhailo.varchenko@gmail.com",   "Михайло Варченко"),
    ("liudmyla.zahorulko@ukr.net",     "Людмила Загорулько"),
    ("oleksandr.tyshchenko@gmail.com", "Олександр Тищенко"),
    ("zoia.yakovlenko@ukr.net",        "Зоя Яковленко"),
    ("fedir.hladchenko@gmail.com",     "Федір Гладченко"),
    ("raisa.lanovyi@ukr.net",          "Раїса Лановий"),
    ("viktor.zamula@gmail.com",        "Віктор Замула"),
    ("uliana.demydenko@ukr.net",       "Уляна Демиденко"),
    ("hennadiy.bereza@gmail.com",      "Геннадій Береза"),
    ("marta.prokopenko@ukr.net",       "Марта Прокопенко"),
]

_SEED_BLUEPRINTS = [
    {"title": "Вічне місто: Рим за 5 днів", "destination": "Рим, Італія", "city": "Rome", "duration": "5 днів", "interests": ["history", "architecture", "food", "art"], "budget_level": "mid", "highlights": ["Колізей", "Ватикан", "Фонтан Треві", "Пантеон"]},
    {"title": "Бюджетний Рим: хостели та тратторії", "destination": "Рим, Італія", "city": "Rome", "duration": "4 дні", "interests": ["history", "food", "architecture"], "budget_level": "budget", "highlights": ["Колізей", "Форум Романум", "Campo de' Fiori"]},
    {"title": "Розкіш у Римі: готель 5★ та Мішлен", "destination": "Рим, Італія", "city": "Rome", "duration": "5 днів", "interests": ["food", "art", "culture"], "budget_level": "premium", "highlights": ["Вілла Боргезе", "VIP Ватикан", "Fine dining"]},
    {"title": "Париж: мистецтво та романтика", "destination": "Париж, Франція", "city": "Paris", "duration": "5 днів", "interests": ["art", "culture", "food", "architecture"], "budget_level": "mid", "highlights": ["Лувр", "Ейфелева вежа", "Монмартр", "Музей д'Орсе"]},
    {"title": "Бюджетний Париж: блошині ринки та бістро", "destination": "Париж, Франція", "city": "Paris", "duration": "4 дні", "interests": ["culture", "history", "food"], "budget_level": "budget", "highlights": ["Сакре-Кер", "Люксембурзький сад", "Маре"]},
    {"title": "Барселона: Гауді та морський бриз", "destination": "Барселона, Іспанія", "city": "Barcelona", "duration": "6 днів", "interests": ["architecture", "beach", "food", "nightlife"], "budget_level": "mid", "highlights": ["Саграда Фамілія", "Парк Гюель", "Борнета", "Барселонета"]},
    {"title": "Прага: готика та крафтове пиво", "destination": "Прага, Чехія", "city": "Prague", "duration": "4 дні", "interests": ["history", "architecture", "culture", "food"], "budget_level": "budget", "highlights": ["Старе місто", "Пражський замок", "Карлів міст"]},
    {"title": "Амстердам: канали та музеї", "destination": "Амстердам, Нідерланди", "city": "Amsterdam", "duration": "4 дні", "interests": ["art", "culture", "history", "cycling"], "budget_level": "mid", "highlights": ["Рейксмузеум", "Ван Гог", "Канальний квартал"]},
    {"title": "Відень: музика та кав'ярні", "destination": "Відень, Австрія", "city": "Vienna", "duration": "4 дні", "interests": ["music", "art", "history", "architecture"], "budget_level": "mid", "highlights": ["Шенбрунн", "Опера", "Бельведер"]},
    {"title": "Лісабон: фаду та пасталь де ната", "destination": "Лісабон, Португалія", "city": "Lisbon", "duration": "5 днів", "interests": ["food", "history", "culture", "photography"], "budget_level": "mid", "highlights": ["Алфама", "Белен", "Синтра"]},
    {"title": "Будапешт: купальні та руїн-бари", "destination": "Будапешт, Угорщина", "city": "Budapest", "duration": "4 дні", "interests": ["history", "nightlife", "architecture", "food"], "budget_level": "budget", "highlights": ["Купальні Сечені", "Рибацький бастіон", "Симпла бар"]},
    {"title": "Будапешт: термальні ванни та парламент", "destination": "Будапешт, Угорщина", "city": "Budapest", "duration": "5 днів", "interests": ["history", "architecture", "food", "culture"], "budget_level": "mid", "highlights": ["Купальні Геллерт", "Парламент", "Варошліґет"]},
    {"title": "Афіни: Акрополь та вуличний фуд", "destination": "Афіни, Греція", "city": "Athens", "duration": "5 днів", "interests": ["history", "architecture", "food", "culture"], "budget_level": "mid", "highlights": ["Акрополь", "Агора", "Плака", "Монастіракі"]},
    {"title": "Стамбул: між Сходом і Заходом", "destination": "Стамбул, Туреччина", "city": "Istanbul", "duration": "5 днів", "interests": ["history", "culture", "food", "architecture"], "budget_level": "mid", "highlights": ["Блакитна мечеть", "Айя-Софія", "Гранд-базар"]},
    {"title": "Дубровнік: перлина Адріатики", "destination": "Дубровнік, Хорватія", "city": "Dubrovnik", "duration": "4 дні", "interests": ["beach", "history", "photography", "sea"], "budget_level": "mid", "highlights": ["Міські мури", "Старе місто", "Пляж Бані"]},
    {"title": "Санторіні: захід сонця та вулкан", "destination": "Санторіні, Греція", "city": "Santorini", "duration": "5 днів", "interests": ["beach", "photography", "romance", "food"], "budget_level": "premium", "highlights": ["Ія", "Фіра", "Ред Біч", "Кратерний круїз"]},
    {"title": "Берлін: мистецтво та вільне місто", "destination": "Берлін, Німеччина", "city": "Berlin", "duration": "5 днів", "interests": ["history", "art", "nightlife", "culture"], "budget_level": "mid", "highlights": ["Берлінський мур", "Музейний острів", "Кройцберг"]},
    {"title": "Краків: Вавель та єврейська спадщина", "destination": "Краків, Польща", "city": "Krakow", "duration": "4 дні", "interests": ["history", "culture", "food", "architecture"], "budget_level": "budget", "highlights": ["Вавельський замок", "Казімеж", "Підземний музей"]},
    {"title": "Таллінн: середньовічна казка", "destination": "Таллінн, Естонія", "city": "Tallinn", "duration": "3 дні", "interests": ["history", "architecture", "culture", "photography"], "budget_level": "budget", "highlights": ["Старе місто ЮНЕСКО", "Тоомпеа", "Ратушна площа"]},
    {"title": "Порто: портвейн та азулежу", "destination": "Порто, Португалія", "city": "Porto", "duration": "4 дні", "interests": ["food", "wine", "architecture", "photography"], "budget_level": "mid", "highlights": ["Болья", "Рібейра", "Підвали Гайя"]},
    {"title": "Флоренція: ренесанс у кожній вулиці", "destination": "Флоренція, Італія", "city": "Florence", "duration": "4 дні", "interests": ["art", "history", "food", "architecture"], "budget_level": "mid", "highlights": ["Уффіці", "Купол Дуомо", "Понте Веккіо"]},
    {"title": "Венеція: лагуна та гондоли", "destination": "Венеція, Італія", "city": "Venice", "duration": "3 дні", "interests": ["architecture", "romance", "art", "photography"], "budget_level": "mid", "highlights": ["Площа Сан-Марко", "Гранд-канал", "Мурано"]},
    {"title": "Мюнхен: Октоберфест та Альпи", "destination": "Мюнхен, Німеччина", "city": "Munich", "duration": "4 дні", "interests": ["food", "culture", "beer", "nature"], "budget_level": "mid", "highlights": ["Марієнплац", "Англійський сад", "Нойшванштайн"]},
    {"title": "Брюгге: шоколад та середньовіччя", "destination": "Брюгге, Бельгія", "city": "Bruges", "duration": "3 дні", "interests": ["history", "architecture", "food", "culture"], "budget_level": "mid", "highlights": ["Ринкова площа", "Пивоварня", "Канали"]},
    {"title": "Любляна та озеро Блед", "destination": "Любляна, Словенія", "city": "Ljubljana", "duration": "4 дні", "interests": ["nature", "architecture", "hiking", "photography"], "budget_level": "budget", "highlights": ["Замок Любляна", "Озеро Блед", "Постойна печера"]},
    {"title": "Гірська Швейцарія: Юнгфрауйох та Берн", "destination": "Берн, Швейцарія", "city": "Bern", "duration": "5 днів", "interests": ["nature", "hiking", "architecture", "photography"], "budget_level": "premium", "highlights": ["Юнгфрауйох", "Грінделвальд", "Берн Альтштадт"]},
    {"title": "Коста-дель-Соль: пляжний відпочинок", "destination": "Малага, Іспанія", "city": "Malaga", "duration": "7 днів", "interests": ["beach", "relaxation", "food", "history"], "budget_level": "mid", "highlights": ["Алькасаба", "Нерха", "Ронда"]},
    {"title": "Ісландія: фіорди та Північне сяйво", "destination": "Рейк'явік, Ісландія", "city": "Reykjavik", "duration": "7 днів", "interests": ["nature", "adventure", "photography", "hiking"], "budget_level": "premium", "highlights": ["Блакитна лагуна", "Золоте кільце", "Скоґафосс"]},
    {"title": "Верона та озеро Гарда", "destination": "Верона, Італія", "city": "Verona", "duration": "5 днів", "interests": ["romance", "history", "food", "nature"], "budget_level": "mid", "highlights": ["Арена ді Верона", "Балкон Джульєтти", "Гарда"]},
    {"title": "Севілья: фламенко та тапас", "destination": "Севілья, Іспанія", "city": "Seville", "duration": "4 дні", "interests": ["culture", "food", "history", "art"], "budget_level": "mid", "highlights": ["Алькасар", "Хіральда", "Барріо Санта-Крус"]},
    {"title": "Единбург та гірська Шотландія", "destination": "Единбург, Велика Британія", "city": "Edinburgh", "duration": "5 днів", "interests": ["history", "nature", "culture", "hiking"], "budget_level": "mid", "highlights": ["Единбурзький замок", "Артурс Сіт", "Хайлендс"]},
    {"title": "Лондон: класика та сучасність", "destination": "Лондон, Велика Британія", "city": "London", "duration": "6 днів", "interests": ["culture", "history", "art", "shopping"], "budget_level": "premium", "highlights": ["Тауер", "Британський музей", "Тейт Модерн"]},
    {"title": "Дублін та кліфи Мохер", "destination": "Дублін, Ірландія", "city": "Dublin", "duration": "5 днів", "interests": ["culture", "nature", "history", "food"], "budget_level": "mid", "highlights": ["Кліфи Мохер", "Замок Дублін", "Гіннесс Сторегаус"]},
    {"title": "Рига: ар-нуво та Балтійське море", "destination": "Рига, Латвія", "city": "Riga", "duration": "3 дні", "interests": ["history", "architecture", "culture", "food"], "budget_level": "budget", "highlights": ["Ар-нуво квартал", "Старе місто", "Центральний ринок"]},
    {"title": "Копенгаген: дизайн та гіге", "destination": "Копенгаген, Данія", "city": "Copenhagen", "duration": "4 дні", "interests": ["design", "food", "cycling", "culture"], "budget_level": "premium", "highlights": ["Нюхавн", "Тіволі", "Луїзіана музей"]},
    {"title": "Брюссель: вафлі та Гранд-Плас", "destination": "Брюссель, Бельгія", "city": "Brussels", "duration": "3 дні", "interests": ["food", "architecture", "culture", "history"], "budget_level": "budget", "highlights": ["Гранд-Плас", "Атоміум", "Музей Магрітта"]},
    {"title": "Варшава: сучасність та відбудоване минуле", "destination": "Варшава, Польща", "city": "Warsaw", "duration": "4 дні", "interests": ["history", "culture", "food", "architecture"], "budget_level": "budget", "highlights": ["Старе місто", "Музей Варшавського повстання", "Лазєнки"]},
    {"title": "Відпочинок у Барселоні: пляж та Гауді", "destination": "Барселона, Іспанія", "city": "Barcelona", "duration": "7 днів", "interests": ["beach", "architecture", "food", "culture"], "budget_level": "premium", "highlights": ["Саграда Фамілія", "Сітжес", "Тібідабо"]},
    {"title": "Мальта: середземноморська перлина", "destination": "Валетта, Мальта", "city": "Valletta", "duration": "5 днів", "interests": ["history", "beach", "architecture", "photography"], "budget_level": "mid", "highlights": ["Валетта ЮНЕСКО", "Блакитна лагуна Комі", "Мдіна"]},
    {"title": "Природна Норвегія: фіорди та водоспади", "destination": "Берген, Норвегія", "city": "Bergen", "duration": "6 днів", "interests": ["nature", "hiking", "photography", "adventure"], "budget_level": "premium", "highlights": ["Фіорд Соґне", "Флом", "Бриксдальсбреен"]},
]

_SEED_REVIEW_COMMENTS = [
    "Чудовий маршрут! Все продумано до дрібниць, жодної зайвої хвилини.",
    "Дуже зручно, що є конкретні поради. Скористалася й не пошкодувала.",
    "Відмінний баланс між відомими пам'ятками та прихованими куточками.",
    "Маршрут перевершив очікування. Рекомендую всім, хто їде вперше.",
    "Зупинялися в готелі з рекомендації — ціна/якість ідеальні.",
    "Ресторани — вогонь! Особливо вечірня локація. Запишіть адресу!",
    "Компактний маршрут, не втомлюєшся, але бачиш усе найголовніше.",
    "Трохи скоригувала під себе, але основа чудова. Дякую!",
    "Вперше подорожувала без туроператора — цей маршрут дуже допоміг.",
    "Бюджет вийшов майже точно як розраховано. Дуже точно!",
    "Захопливо! Особливо порадувала підказка щодо раннього відвідування.",
    "Ідеально підходить для пар. Романтична атмосфера на кожному кроці.",
    "Дякую за детальні поради! Без них точно б щось пропустила.",
    "Супер для першого відвідування. Потім захочеться ще більше часу.",
    "Ідеально для тих, хто любить і культуру, і смачну їжу.",
    "Логістика продумана, все поруч — мінімум часу на транспорт.",
    "Куплені квитки онлайн заздалегідь, як порадили — все чудово.",
    "Місцева кухня — окрема пригода. Рекомендований ресторан запам'яталася.",
    "Гарний вибір для цього бюджету. Якість відповідає ціні.",
    "Маршрут чітко структурований: ранок — активно, вечір — розслаблено.",
    "Поїхала з подругою — обидві в захваті. Обов'язково повернемося!",
    "Кілька локацій виявились закритими, але план Б завжди знайшовся.",
    "Маршрут збалансований між активністю та відпочинком — саме те.",
    "Використали майже повністю. Задоволені на всі 100%!",
    "Чудові підказки щодо місцевого транспорту — зекономили купу часу.",
    "Не очікувала такого рівня деталізації — приємно здивована.",
    "Взяли цей маршрут як основу і додали кілька своїх точок — вийшло ідеально.",
    "Дуже реалістичний бюджет, нічого зайвого. Рекомендую!",
    "Маршрут підходить і для соло-мандрівниць — відчуваєшся впевнено.",
    "Варто бронювати деякі місця заздалегідь, але маршрут — 10/10.",
]


def _seed_demo_data():
    """Populate DB with fake users, saved routes, and reviews for recommendation demo."""
    import random
    random.seed(42)

    with _get_db() as db:
        existing = db.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()["cnt"]
        if existing >= 10:
            return

    pw_hash = generate_password_hash("demo1234")

    with _get_db() as db:
        user_ids = []
        for email, name in _SEED_USERS:
            db.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (%s,%s,%s)"
                " ON CONFLICT (email) DO NOTHING",
                (email, name, pw_hash)
            )
        db.commit()
        for email, _ in _SEED_USERS:
            row = db.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone()
            if row:
                user_ids.append(row["id"])

        if not user_ids:
            return

        route_ids = []
        blueprints = _SEED_BLUEPRINTS[:]
        for uid in user_ids:
            num = random.randint(2, 3)
            chosen = random.sample(blueprints, min(num, len(blueprints)))
            for bp in chosen:
                rd = json.dumps({
                    "title": bp["title"],
                    "destination": bp["destination"],
                    "destination_city": bp["city"],
                    "duration": bp["duration"],
                    "interests": bp["interests"],
                    "budget_level": bp["budget_level"],
                    "highlights": bp["highlights"],
                }, ensure_ascii=False)
                db.execute(
                    "INSERT INTO saved_routes"
                    " (user_id, title, destination, duration, route_data, interests, budget_level)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (uid, bp["title"], bp["destination"], bp["duration"],
                     rd, json.dumps(bp["interests"]), bp["budget_level"])
                )
                rid = db.fetchone()["id"]
                route_ids.append((rid, uid))

        for rid, owner_uid in route_ids:
            reviewers = [u for u in user_ids if u != owner_uid]
            chosen_reviewers = random.sample(reviewers, min(random.randint(3, 6), len(reviewers)))
            for rev_uid in chosen_reviewers:
                rating = random.choices([3, 4, 4, 5, 5, 5], k=1)[0]
                comment = random.choice(_SEED_REVIEW_COMMENTS)
                db.execute(
                    "INSERT INTO reviews (user_id, route_id, rating, comment)"
                    " VALUES (%s,%s,%s,%s) ON CONFLICT (user_id, route_id) DO NOTHING",
                    (rev_uid, rid, rating, comment)
                )

        db.commit()


_seed_demo_data()


def get_recommendations(user_id, interests, budget_level, limit=5):
    """Content-based filtering: score routes by interest overlap, budget match, and avg rating."""
    try:
        with _get_db() as db:
            rows = db.execute("""
                SELECT
                    sr.id, sr.title, sr.destination, sr.duration,
                    sr.interests, sr.budget_level,
                    COALESCE(AVG(rv.rating), 3.5) as avg_rating,
                    COUNT(rv.id) as review_count
                FROM saved_routes sr
                LEFT JOIN reviews rv ON rv.route_id = sr.id
                WHERE sr.user_id != COALESCE(?, -1)
                GROUP BY sr.id
                ORDER BY avg_rating DESC
                LIMIT 300
            """, (user_id,)).fetchall()
    except Exception:
        return []

    # Normalise user interests to lowercase for case-insensitive matching
    interest_set = set(i.lower().strip() for i in (interests or []))

    scored = []
    for row in rows:
        try:
            route_interests = set(json.loads(row['interests'] or '[]'))
        except Exception:
            route_interests = set()

        # Interest overlap score (0–50 pts): Jaccard-style proportion of matching interests
        if interest_set:
            overlap = len(interest_set & route_interests)
            interest_score = (overlap / len(interest_set)) * 50
        else:
            # No preferences specified — treat as neutral match
            interest_score = 25

        # Budget match score (0–20 pts): exact match=20, adjacent level=10, opposite=0
        rl = row['budget_level'] or 'mid'
        if rl == budget_level:
            budget_score = 20
        elif {rl, budget_level} <= {'budget', 'mid'} or {rl, budget_level} <= {'mid', 'premium'}:
            budget_score = 10
        else:
            budget_score = 0

        # Rating score (0–25 pts): scaled from 0–5 star average
        rating_score = (float(row['avg_rating']) / 5.0) * 25

        # Popularity score (0–5 pts): capped at 20 reviews to avoid domination by old routes
        pop_score = min(row['review_count'] / 20.0, 1.0) * 5

        total = interest_score + budget_score + rating_score + pop_score

        scored.append({
            "id": row['id'],
            "title": row['title'],
            "destination": row['destination'],
            "duration": row['duration'],
            "interests": list(route_interests),
            "budget_level": rl,
            "avg_rating": round(float(row['avg_rating']), 1),
            "review_count": row['review_count'],
            "score": round(total, 1),
        })

    # Sort all candidates by composite score descending
    scored.sort(key=lambda x: x['score'], reverse=True)

    # Deduplicate by title — the same blueprint may be saved by multiple users;
    # keep only the highest-scoring copy so results look diverse
    seen_titles = set()
    unique = []
    for r in scored:
        key = r['title']
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(r)
    return unique[:limit]


RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "booking-com15.p.rapidapi.com"

RAPIDAPI_GOOGLE_HOST = "google-map-places-new-v2.p.rapidapi.com"

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

FSQ_API_KEY = os.environ.get("FSQ_API_KEY", "")

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")

# ── FOOD PHOTOS via Pexels search API ──
CUISINE_QUERIES = {
    'italian':       'Italian restaurant cozy interior pasta warm lighting',
    'pasta':         'Italian restaurant pasta interior rustic',
    'pizza':         'pizzeria restaurant interior rustic wood oven',
    'japanese':      'Japanese restaurant sushi bar interior minimalist',
    'sushi':         'sushi restaurant elegant Japanese interior',
    'ramen':         'ramen noodle restaurant Japan cozy interior',
    'french':        'French bistro restaurant interior cozy elegant',
    'seafood':       'seafood restaurant ocean interior fresh fish',
    'fish':          'fish restaurant interior maritime decor',
    'steak':         'steakhouse restaurant warm lighting dark interior',
    'grill':         'grill restaurant cozy interior fire',
    'bbq':           'barbecue restaurant rustic interior smoky',
    'burger':        'burger restaurant casual interior neon',
    'american':      'American diner restaurant interior retro',
    'mediterranean': 'Mediterranean restaurant white walls blue decor',
    'greek':         'Greek taverna restaurant interior blue white',
    'spanish':       'Spanish tapas bar interior wooden tables wine',
    'tapas':         'tapas bar interior Spain rustic',
    'mexican':       'Mexican restaurant colorful interior fiesta',
    'thai':          'Thai restaurant exotic interior golden decor',
    'indian':        'Indian restaurant colorful interior spices decor',
    'chinese':       'Chinese restaurant elegant red lanterns interior',
    'asian':         'Asian fusion restaurant interior modern',
    'vietnamese':    'Vietnamese restaurant pho noodle interior bamboo',
    'korean':        'Korean bbq restaurant grill table interior',
    'vegetarian':    'vegetarian restaurant fresh plants interior light',
    'vegan':         'vegan restaurant plant-based green interior',
    'cafe':          'cozy cafe interior coffee wood warm lighting',
    'coffee':        'specialty coffee shop interior barista',
    'bakery':        'artisan bakery interior pastries bread display',
    'dessert':       'dessert cafe sweet pastry interior elegant',
    'ukrainian':     'Ukrainian restaurant traditional folk interior',
    'european':      'European restaurant elegant interior fine dining',
    'moroccan':      'Moroccan restaurant interior lanterns tiles colorful',
    'turkish':       'Turkish restaurant interior traditional ceramic',
}
_PHOTO_CACHE: dict = {}


def _fetch_cuisine_photo(cuisine: str) -> bytes | None:
    """Choose a Pexels search query based on cuisine keyword and fetch the photo."""
    cuisine_lower = (cuisine or '').lower()
    query = None
    for key, q in CUISINE_QUERIES.items():
        if key in cuisine_lower:
            query = q
            break
    if not query:
        query = f'{cuisine} restaurant interior cozy' if cuisine else 'cozy restaurant interior food warm lighting'
    return _fetch_pexels_photo(query)


@app.route("/api/place-photo")
def place_photo():
    """Fetch a photo for a tourist attraction via Google Places API."""
    place = request.args.get("place", "").strip()
    city  = request.args.get("city",  "").strip()
    if not place:
        return '', 400

    query = f"{place} {city}".strip() if city else place
    data = _fetch_google_place_photo(query)
    if not data and city:
        # retry with just place name
        data = _fetch_google_place_photo(place)
    if not data:
        return '', 404

    resp = Response(data, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


@app.route("/api/food-photo")
def food_photo():
    """Return a cuisine-themed photo fetched from Pexels."""
    cuisine = request.args.get("cuisine", "")
    data = _fetch_cuisine_photo(cuisine)
    if not data:
        return '', 404
    resp = Response(data, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


# ── RESTAURANT PHOTOS ──
_RESTAURANT_PHOTO_CACHE: dict = {}


# Types that confirm a place is a food establishment
_FOOD_TYPES = {
    'restaurant', 'food', 'meal_takeaway', 'meal_delivery', 'cafe',
    'bakery', 'bar', 'pizza_restaurant', 'hamburger_restaurant',
    'japanese_restaurant', 'italian_restaurant', 'chinese_restaurant',
    'french_restaurant', 'seafood_restaurant', 'steak_house',
    'vegetarian_restaurant', 'vegan_restaurant', 'coffee_shop',
    'ice_cream_shop', 'dessert_shop', 'fast_food_restaurant',
    'sandwich_shop', 'brunch_restaurant', 'breakfast_restaurant',
    'wine_bar', 'pub', 'night_club', 'american_restaurant',
    'greek_restaurant', 'turkish_restaurant', 'indian_restaurant',
    'thai_restaurant', 'korean_restaurant', 'mediterranean_restaurant',
    'lebanese_restaurant', 'mexican_restaurant', 'spanish_restaurant',
    'ramen_restaurant', 'sushi_restaurant',
}

# Quota guard: after first 429, skip Google Places until midnight UTC
_google_quota_exceeded_until: float = 0.0


def _google_places_quota_ok() -> bool:
    """Return True when the Google Places daily quota has not been exhausted."""
    return time.time() > _google_quota_exceeded_until


def _google_places_mark_quota_exceeded() -> None:
    """Block Google Places requests until midnight UTC after a 429 response."""
    global _google_quota_exceeded_until
    import datetime as _dt
    now = _dt.datetime.utcnow()
    midnight = (_dt.datetime(now.year, now.month, now.day)
                + _dt.timedelta(days=1))
    _google_quota_exceeded_until = midnight.timestamp()


def _google_search_food_place(text_query: str):
    """Search Google Places and return (place_id, photos) only if result is a food place."""
    try:
        hdrs = {
            'x-rapidapi-key': RAPIDAPI_KEY,
            'x-rapidapi-host': RAPIDAPI_GOOGLE_HOST,
            'Content-Type': 'application/json',
            'X-Goog-FieldMask': 'places.id,places.photos,places.types,places.displayName',
        }
        body = json.dumps({'textQuery': text_query}).encode()
        conn = http.client.HTTPSConnection(RAPIDAPI_GOOGLE_HOST, timeout=8)
        conn.request("POST", "/v1/places:searchText", body=body, headers=hdrs)
        res = conn.getresponse()
        status = res.status
        raw = res.read().decode()

        if status == 429:
            _google_places_mark_quota_exceeded()
            return None, None

        if status != 200:
            return None, None

        data = json.loads(raw)

        # Check for API-level error (quota exceeded, etc.)
        if 'error' in data:
            err = data['error']
            if err.get('status') == 'RESOURCE_EXHAUSTED' or err.get('code') == 429:
                _google_places_mark_quota_exceeded()
            return None, None

        for place in data.get('places', [])[:5]:
            types  = set(place.get('types', []))
            photos = place.get('photos', [])
            if not types.intersection(_FOOD_TYPES):
                continue
            if not photos:
                continue
            return place.get('id', ''), photos

        return None, None
    except Exception:
        return None, None


def _score_photo(photo: dict, index: int) -> float:
    """Score a Google Places photo by aspect ratio; landscape interiors score highest."""
    w = photo.get('widthPx', 0)
    h = photo.get('heightPx', 0)
    if w <= 0 or h <= 0:
        return 0.5 - index * 0.05   # no dimension info — slight preference for earlier photos

    ratio = w / h
    if ratio < 0.8:          # portrait — menu/receipt
        score = -1.0
    elif ratio > 2.8:        # ultra-wide — panorama/landscape
        score = -0.5
    elif 1.1 <= ratio <= 1.9: # sweet spot — interior/food
        score = 1.0
    else:                     # acceptable landscape
        score = 0.3

    return score - index * 0.03   # small penalty for later photos


def _google_fetch_photo_bytes(place_id: str, photos: list) -> bytes | None:
    """Download the best-scoring photo for a place (by aspect ratio heuristic)."""
    if not photos:
        return None

    scored = sorted(enumerate(photos[:10]), key=lambda t: -_score_photo(t[1], t[0]))
    top_photos = [p for _, p in scored[:5]]

    for photo in top_photos:
        try:
            photo_full = photo.get('name', '')
            photo_ref  = photo_full.split('/photos/')[-1] if '/photos/' in photo_full else photo_full
            if not photo_ref:
                continue

            conn = http.client.HTTPSConnection(RAPIDAPI_GOOGLE_HOST, timeout=8)
            path = (f"/v1/places/{place_id}/photos/{photo_ref}/media"
                    f"?maxWidthPx=400&maxHeightPx=400&skipHttpRedirect=true")
            conn.request("GET", path, headers={
                'x-rapidapi-key': RAPIDAPI_KEY,
                'x-rapidapi-host': RAPIDAPI_GOOGLE_HOST,
            })
            res = conn.getresponse()
            photo_json = json.loads(res.read().decode())
            photo_uri = photo_json.get('photoUri', '')
            if not photo_uri:
                continue

            img_req = urllib.request.Request(photo_uri, headers={'User-Agent': 'WandrApp/1.0'})
            with urllib.request.urlopen(img_req, timeout=10, context=_SSL_CTX) as img_r:
                return img_r.read()
        except Exception:
            pass
    return None


def _fetch_rapidapi_google_photo(maps_query: str, cuisine: str = '') -> bytes | None:
    """Real venue photos via Google Maps Places (New) on RapidAPI."""
    if not maps_query and not cuisine:
        return None

    if not _google_places_quota_ok():
        return None  # daily quota exceeded — skip silently until midnight UTC

    cache_key = f"rgoog:{(maps_query or cuisine)[:80].lower()}"
    if cache_key in _RESTAURANT_PHOTO_CACHE:
        return _RESTAURANT_PHOTO_CACHE[cache_key]

    place_id, photos = _google_search_food_place(maps_query) if maps_query else (None, None)

    # If not found or not a food place → search "cuisine restaurant city"
    if not place_id and (cuisine or maps_query):
        # Extract likely city: last 1-2 words of maps_query (e.g. "Vapiano Rome" → "Rome")
        words = maps_query.strip().split() if maps_query else []
        city_hint = ' '.join(words[-2:]) if len(words) >= 2 else (words[0] if words else '')
        fallback_q = f"{cuisine} restaurant {city_hint}".strip() if cuisine else f"restaurant {city_hint}".strip()
        place_id, photos = _google_search_food_place(fallback_q)

    if not place_id or not photos:
        return None

    img_bytes = _google_fetch_photo_bytes(place_id, photos)
    if img_bytes:
        _RESTAURANT_PHOTO_CACHE[cache_key] = img_bytes
    return img_bytes


def _fetch_google_place_photo(maps_query: str) -> bytes | None:
    """Fetch a real venue photo via Google Places API (same source as Google Maps)."""
    if not GOOGLE_API_KEY or not maps_query:
        return None

    cache_key = f"goog:{maps_query[:80].lower()}"
    if cache_key in _RESTAURANT_PHOTO_CACHE:
        return _RESTAURANT_PHOTO_CACHE[cache_key]

    try:
        # Step 1: Text search → place_id
        params = urllib.parse.urlencode({
            'query': maps_query,
            'key': GOOGLE_API_KEY,
            'fields': 'place_id',
            'language': 'en',
        })
        req = urllib.request.Request(
            f"https://maps.googleapis.com/maps/api/place/textsearch/json?{params}",
            headers={'User-Agent': 'WandrApp/1.0'},
        )
        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as r:
            data = json.loads(r.read().decode())

        results = data.get('results', [])
        if not results:
            return None

        place_id = results[0]['place_id']

        # Step 2: Place details → photo_reference
        params2 = urllib.parse.urlencode({
            'place_id': place_id,
            'fields': 'photos',
            'key': GOOGLE_API_KEY,
        })
        req2 = urllib.request.Request(
            f"https://maps.googleapis.com/maps/api/place/details/json?{params2}",
            headers={'User-Agent': 'WandrApp/1.0'},
        )
        with urllib.request.urlopen(req2, timeout=8, context=_SSL_CTX) as r2:
            detail = json.loads(r2.read().decode()).get('result', {})

        photos = detail.get('photos', [])
        if not photos:
            return None

        photo_ref = photos[0]['photo_reference']

        # Step 3: Download photo (Google redirects to CDN image)
        params3 = urllib.parse.urlencode({
            'maxwidth': '400',
            'photo_reference': photo_ref,
            'key': GOOGLE_API_KEY,
        })
        photo_req = urllib.request.Request(
            f"https://maps.googleapis.com/maps/api/place/photo?{params3}",
            headers={'User-Agent': 'WandrApp/1.0'},
        )
        with urllib.request.urlopen(photo_req, timeout=10, context=_SSL_CTX) as img_r:
            img_data = img_r.read()

        _RESTAURANT_PHOTO_CACHE[cache_key] = img_data
        return img_data

    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def _fetch_foursquare_photo(maps_query: str) -> bytes | None:
    """Fetch a venue photo from Foursquare Places API by text query."""
    if not FSQ_API_KEY or not maps_query:
        return None

    cache_key = maps_query.lower().strip()
    if cache_key in _RESTAURANT_PHOTO_CACHE:
        return _RESTAURANT_PHOTO_CACHE[cache_key]

    try:
        # Step 1: find venue by text query
        params = urllib.parse.urlencode({'query': maps_query, 'limit': '1'})
        req = urllib.request.Request(
            f"https://api.foursquare.com/v3/places/search?{params}",
            headers={'Authorization': FSQ_API_KEY, 'Accept': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as r:
            results = json.loads(r.read().decode()).get('results', [])
        if not results:
            return None

        fsq_id = results[0]['fsq_id']

        # Step 2: get photos for that venue
        req2 = urllib.request.Request(
            f"https://api.foursquare.com/v3/places/{fsq_id}/photos?limit=1",
            headers={'Authorization': FSQ_API_KEY, 'Accept': 'application/json'},
        )
        with urllib.request.urlopen(req2, timeout=8, context=_SSL_CTX) as r2:
            photos = json.loads(r2.read().decode())
        if not photos:
            return None

        p = photos[0]
        photo_url = f"{p['prefix']}original{p['suffix']}"

        # Step 3: fetch the image bytes
        img_req = urllib.request.Request(photo_url, headers={'User-Agent': 'WandrApp/1.0'})
        with urllib.request.urlopen(img_req, timeout=8, context=_SSL_CTX) as img_r:
            img_data = img_r.read()

        _RESTAURANT_PHOTO_CACHE[cache_key] = img_data
        return img_data

    except Exception:
        return None


def _fetch_pexels_photo(query: str) -> bytes | None:
    """Search Pexels for a landscape photo matching query and return image bytes."""
    if not PEXELS_API_KEY or not query:
        return None

    cache_key = f"px:{query[:80].lower()}"
    if cache_key in _RESTAURANT_PHOTO_CACHE:
        return _RESTAURANT_PHOTO_CACHE[cache_key]

    try:
        params = urllib.parse.urlencode({
            'query': query, 'per_page': '5', 'orientation': 'landscape'
        })
        req = urllib.request.Request(
            f"https://api.pexels.com/v1/search?{params}",
            headers={'Authorization': PEXELS_API_KEY}
        )
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
            body = r.read().decode()
        photos = json.loads(body).get('photos', [])
        if not photos:
            return None

        idx = int(hashlib.md5(query.encode()).hexdigest(), 16) % len(photos)
        photo_url = photos[idx]['src']['medium']

        img_req = urllib.request.Request(photo_url, headers={
            'User-Agent': 'WandrApp/1.0',
            'Referer': 'https://www.pexels.com/'
        })
        with urllib.request.urlopen(img_req, timeout=10, context=_SSL_CTX) as img_r:
            img_data = img_r.read()

        _RESTAURANT_PHOTO_CACHE[cache_key] = img_data
        return img_data
    except Exception:
        return None


_PROXY_ALLOWED_HOSTS = {
    'cf.bstatic.com', 'q.bstatic.com', 'r.bstatic.com',
    'images.pexels.com', 'photos.pexels.com',
}

@app.route("/api/proxy-image")
def proxy_image():
    """Proxy an image from an allowlisted CDN host to avoid mixed-content issues."""
    url = request.args.get("url", "").strip()
    if not url:
        return '', 400
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc not in _PROXY_ALLOWED_HOSTS:
            return '', 403
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Referer': 'https://www.booking.com/',
        })
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
            content_type = r.headers.get('Content-Type', 'image/jpeg')
            data = r.read()
        resp = Response(data, mimetype=content_type)
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp
    except Exception:
        return '', 404


_MEALDB_AREA_MAP = {
    'italian': 'Italian', 'pizza': 'Italian', 'pasta': 'Italian', 'risotto': 'Italian',
    'japanese': 'Japanese', 'sushi': 'Japanese', 'ramen': 'Japanese',
    'chinese': 'Chinese', 'dim sum': 'Chinese',
    'indian': 'Indian', 'curry': 'Indian',
    'french': 'French', 'bistro': 'French',
    'mexican': 'Mexican', 'tacos': 'Mexican',
    'american': 'American', 'burger': 'American', 'bbq': 'American',
    'british': 'British', 'pub': 'British',
    'thai': 'Thai',
    'greek': 'Greek',
    'spanish': 'Spanish', 'tapas': 'Spanish', 'paella': 'Spanish',
    'turkish': 'Turkish', 'kebab': 'Turkish',
    'moroccan': 'Moroccan',
    'vietnamese': 'Vietnamese', 'pho': 'Vietnamese',
    'canadian': 'Canadian',
    'jamaican': 'Jamaican',
    'polish': 'Polish',
    'portuguese': 'Portuguese',
    'russian': 'Russian',
    'ukrainian': 'Polish',
}


def _fetch_themealdb_photo(cuisine: str, restaurant_name: str = '') -> bytes | None:
    """Free food photos from TheMealDB — no API key needed."""
    cuisine_lower = (cuisine or '').lower()
    area = next((v for k, v in _MEALDB_AREA_MAP.items() if k in cuisine_lower), 'Italian')

    cache_key = f"mealdb:{area.lower()}"
    if cache_key in _RESTAURANT_PHOTO_CACHE:
        return _RESTAURANT_PHOTO_CACHE[cache_key]

    try:
        url = f"https://www.themealdb.com/api/json/v1/1/filter.php?a={area}"
        req = urllib.request.Request(url, headers={'User-Agent': 'WandrApp/1.0'})
        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as r:
            meals = json.loads(r.read().decode()).get('meals') or []

        if not meals:
            return None

        # Deterministic pick so the same cuisine always shows the same dish
        idx = int(hashlib.md5(cuisine.encode()).hexdigest(), 16) % min(len(meals), 20)
        thumb_url = meals[idx].get('strMealThumb', '')
        if not thumb_url:
            return None

        img_req = urllib.request.Request(
            thumb_url + '/preview',
            headers={'User-Agent': 'WandrApp/1.0'},
        )
        with urllib.request.urlopen(img_req, timeout=8, context=_SSL_CTX) as img_r:
            img_data = img_r.read()

        _RESTAURANT_PHOTO_CACHE[cache_key] = img_data
        return img_data
    except Exception:
        return None


def _fetch_picsum_photo(seed: str) -> bytes | None:
    """Guaranteed fallback — Lorem Picsum, no API key needed."""
    clean = hashlib.md5(seed.encode()).hexdigest()[:10]
    cache_key = f"picsum:{clean}"
    if cache_key in _RESTAURANT_PHOTO_CACHE:
        return _RESTAURANT_PHOTO_CACHE[cache_key]
    try:
        url = f"https://picsum.photos/seed/{clean}/400/240"
        req = urllib.request.Request(url, headers={'User-Agent': 'WandrApp/1.0'})
        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as r:
            data = r.read()
        _RESTAURANT_PHOTO_CACHE[cache_key] = data
        return data
    except Exception:
        return None


@app.route("/api/restaurant-photo")
def restaurant_photo():
    """Return a restaurant photo from cascading sources: Google → Foursquare → TheMealDB."""
    maps_query = request.args.get("q", "")
    cuisine    = request.args.get("cuisine", "")
    photo_q    = request.args.get("photo_q", "")

    # 1. RapidAPI Google Maps Places — real venue photos
    data = _fetch_rapidapi_google_photo(maps_query, cuisine)

    # 2. Google Places API (direct key, if configured)
    if not data:
        data = _fetch_google_place_photo(maps_query)

    # 3. Foursquare
    if not data:
        data = _fetch_foursquare_photo(maps_query)

    # 4. TheMealDB — free dish photo by cuisine type (no key needed)
    if not data:
        data = _fetch_themealdb_photo(cuisine, maps_query)

    if not data:
        return '', 404
    resp = Response(data, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

# Platform catalogue — keyed by short id used in AI response
TRANSPORT_LINK_TEMPLATES = {
    "google_flights": {"name": "Google Flights", "url_tpl": "https://www.google.com/travel/flights?q=flights+from+{from}+to+{to}", "bg": "#4285f4"},
    "kiwi":           {"name": "Kiwi.com",        "url_tpl": "https://www.kiwi.com/en/search/results/{from}/{to}",                  "bg": "#e16336"},
    "skyscanner":     {"name": "Skyscanner",       "url_tpl": "https://www.skyscanner.net/transport/flights/{from}/{to}/",           "bg": "#0770e3"},
    "wizzair":        {"name": "Wizz Air",          "url_tpl": "https://wizzair.com/",                                               "bg": "#c6007e"},
    "ryanair":        {"name": "Ryanair",           "url_tpl": "https://www.ryanair.com/",                                           "bg": "#073590"},
    "easyjet":        {"name": "easyJet",           "url_tpl": "https://www.easyjet.com/",                                           "bg": "#ff6600"},
    "trainline":      {"name": "Trainline",         "url_tpl": "https://www.thetrainline.com/",                                      "bg": "#00b200"},
    "raileurope":     {"name": "Rail Europe",       "url_tpl": "https://www.raileurope.com/",                                        "bg": "#e30613"},
    "omio":           {"name": "Omio",              "url_tpl": "https://www.omio.com/",                                              "bg": "#00305e"},
    "uz":             {"name": "Укрзалізниця",      "url_tpl": "https://www.uz.gov.ua/",                                             "bg": "#1a56db"},
    "flixbus":        {"name": "FlixBus",           "url_tpl": "https://www.flixbus.com/",                                           "bg": "#73d700"},
    "blablabus":      {"name": "BlaBlaBus",         "url_tpl": "https://www.blablacar.com/bus",                                      "bg": "#00b2ff"},
    "busfor":         {"name": "Busfor",            "url_tpl": "https://busfor.ua/",                                                  "bg": "#ff6b00"},
    "ecolines":       {"name": "Ecolines",          "url_tpl": "https://ecolines.net/",                                              "bg": "#e95a0c"},
    "infobus":        {"name": "Infobus",           "url_tpl": "https://infobus.eu/",                                                 "bg": "#009fe3"},
    "rome2rio":       {"name": "Rome2Rio",          "url_tpl": "https://www.rome2rio.com/s/{from}/{to}",                             "bg": "#ff6319"},
    "directferries":  {"name": "Direct Ferries",   "url_tpl": "https://www.directferries.co.uk/",                                    "bg": "#1a4694"},
    "ferryscanner":   {"name": "Ferryscanner",      "url_tpl": "https://www.ferryscanner.com/",                                      "bg": "#0070f3"},
}

_UKRAINE_KEYWORDS = {
    'київ','kyiv','kiev','харків','kharkiv','дніпро','dnipro','одеса','odesa','одесса',
    'запоріжжя','zaporizhzhia','львів','lviv','вінниця','vinnytsia','полтава','poltava',
    'черкаси','cherkasy','суми','sumy','миколаїв','mykolaiv','херсон','kherson',
    'україна','ukraine','ua',
}

def _is_ukraine(city: str) -> bool:
    """Return True if the city string matches a Ukrainian city or country keyword."""
    return any(kw in city.lower() for kw in _UKRAINE_KEYWORDS)


def _resolve_leg_links(leg: dict) -> list:
    """Turn platform ids in leg['platforms'] into full {name, url, bg} dicts."""
    frm = urllib.parse.quote(leg.get("from", ""), safe='')
    to  = urllib.parse.quote(leg.get("to",   ""), safe='')
    result = []
    for pid in leg.get("platforms", []):
        tpl = TRANSPORT_LINK_TEMPLATES.get(pid)
        if not tpl:
            continue
        url = (tpl["url_tpl"]
               .replace("{from}", frm)
               .replace("{to}",   to))
        result.append({"name": tpl["name"], "url": url, "bg": tpl["bg"]})
    return result


def enrich_transport_routes(routes: list) -> list:
    """Add resolved platform links to every leg in every route."""
    for route in routes:
        for leg in route.get("legs", []):
            leg["links"] = _resolve_leg_links(leg)
    return routes


WMO_CODES = {
    0: ("Ясно", "☀️"), 1: ("Переважно ясно", "🌤️"), 2: ("Мінлива хмарність", "⛅"),
    3: ("Хмарно", "☁️"), 45: ("Туман", "🌫️"), 48: ("Туман з інеєм", "🌫️"),
    51: ("Легка мряка", "🌦️"), 53: ("Мряка", "🌦️"), 55: ("Сильна мряка", "🌧️"),
    61: ("Невеликий дощ", "🌧️"), 63: ("Дощ", "🌧️"), 65: ("Сильний дощ", "🌧️"),
    71: ("Невеликий сніг", "🌨️"), 73: ("Сніг", "❄️"), 75: ("Сильний сніг", "❄️"),
    77: ("Снігова крупа", "🌨️"), 80: ("Короткочасний дощ", "🌦️"), 81: ("Дощові зливи", "🌧️"),
    82: ("Сильні зливи", "⛈️"), 85: ("Снігові зливи", "🌨️"), 86: ("Сильні снігові зливи", "❄️"),
    95: ("Гроза", "⛈️"), 96: ("Гроза з градом", "⛈️"), 99: ("Сильна гроза з градом", "⛈️"),
}

MONTH_CLIMATE = {
    1: "Січень", 2: "Лютий", 3: "Березень", 4: "Квітень",
    5: "Травень", 6: "Червень", 7: "Липень", 8: "Серпень",
    9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень"
}


def get_weather_forecast(lat, lng, start_date, end_date):
    """Get real forecast from Open-Meteo (up to 16 days ahead)"""
    try:
        params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lng,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode,windspeed_10m_max",
            "timezone": "auto",
            "start_date": start_date,
            "end_date": end_date,
        })
        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "WandrApp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        daily = data.get("daily", {})
        dates = daily.get("time", [])
        result = []
        for i, date in enumerate(dates):
            code = daily.get("weathercode", [])[i] if i < len(daily.get("weathercode", [])) else 0
            desc, icon = WMO_CODES.get(code, ("Невідомо", "🌡️"))
            result.append({
                "date": date,
                "temp_max": daily.get("temperature_2m_max", [])[i] if i < len(daily.get("temperature_2m_max", [])) else None,
                "temp_min": daily.get("temperature_2m_min", [])[i] if i < len(daily.get("temperature_2m_min", [])) else None,
                "precipitation": daily.get("precipitation_sum", [])[i] if i < len(daily.get("precipitation_sum", [])) else 0,
                "wind": daily.get("windspeed_10m_max", [])[i] if i < len(daily.get("windspeed_10m_max", [])) else None,
                "description": desc,
                "icon": icon,
                "type": "forecast"
            })
        return result
    except Exception:
        return []


def get_climate_normals(lat, lng, month):
    """Get historical climate normals for a given month via Open-Meteo"""
    try:
        results = []
        current_year = datetime.now().year

        # Collect data from the past 5 years for this month
        for y in range(current_year - 5, current_year):
            import calendar
            last_day = calendar.monthrange(y, month)[1]
            start = f"{y}-{month:02d}-01"
            end = f"{y}-{month:02d}-{last_day}"

            params = urllib.parse.urlencode({
                "latitude": lat,
                "longitude": lng,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
                "timezone": "auto",
                "start_date": start,
                "end_date": end,
            })
            url = f"https://archive-api.open-meteo.com/v1/archive?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "WandrApp/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            daily = data.get("daily", {})
            results.append(daily)

        all_max = []
        all_min = []
        all_precip = []
        all_codes = []

        for daily in results:
            all_max += [t for t in daily.get("temperature_2m_max", []) if t is not None]
            all_min += [t for t in daily.get("temperature_2m_min", []) if t is not None]
            all_precip += [p for p in daily.get("precipitation_sum", []) if p is not None]
            all_codes += [c for c in daily.get("weathercode", []) if c is not None]

        avg_max = round(sum(all_max) / len(all_max), 1) if all_max else None
        avg_min = round(sum(all_min) / len(all_min), 1) if all_min else None
        avg_monthly_precip = round(sum(all_precip), 1) / 5 if all_precip else None

        if all_codes:
            from collections import Counter
            most_common_code = Counter(all_codes).most_common(1)[0][0]
            desc, icon = WMO_CODES.get(most_common_code, ("Змінна погода", "🌤️"))
        else:
            desc, icon = "Змінна погода", "🌤️"

        return {
            "month": month,
            "month_name": MONTH_CLIMATE.get(month, ""),
            "avg_temp_max": avg_max,
            "avg_temp_min": avg_min,
            "avg_monthly_precip": avg_monthly_precip,
            "typical_weather": desc,
            "icon": icon,
            "type": "climate"
        }
    except Exception:
        return None


def search_destination_id(destination):
    """Resolve a destination string to a Booking.com dest_id via RapidAPI."""
    try:
        conn = http.client.HTTPSConnection(RAPIDAPI_HOST)
        headers = {'x-rapidapi-key': RAPIDAPI_KEY, 'x-rapidapi-host': RAPIDAPI_HOST}
        query = urllib.parse.quote(destination)
        conn.request("GET", f"/api/v1/hotels/searchDestination?query={query}", headers=headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        if data.get("status") and data.get("data"):
            for item in data["data"]:
                if item.get("dest_type") in ("city", "region", "country"):
                    return item.get("dest_id"), item.get("dest_type"), item.get("label", destination)
            first = data["data"][0]
            return first.get("dest_id"), first.get("dest_type"), first.get("label", destination)
    except Exception:
        pass
    return None, None, destination


def search_hotels(destination, budget, nights, checkin=None, checkout=None, budget_level="mid"):
    """Search Booking.com for hotels, filter and sort by budget_level, return up to 6 results."""
    try:
        dest_id, dest_type, dest_label = search_destination_id(destination)
        if not dest_id:
            return []

        if not checkin:
            checkin = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        if not checkout:
            checkout = (datetime.now() + timedelta(days=30 + max(nights, 1))).strftime("%Y-%m-%d")

        conn = http.client.HTTPSConnection(RAPIDAPI_HOST)
        headers = {'x-rapidapi-key': RAPIDAPI_KEY, 'x-rapidapi-host': RAPIDAPI_HOST}

        adults = "1" if budget_level == "budget" else "2"

        base_params = {
            "dest_id": dest_id, "search_type": dest_type or "city",
            "arrival_date": checkin, "departure_date": checkout,
            "adults": adults, "room_qty": "1", "page_number": "1",
            "currency_code": "EUR",
        }

        def _do_hotel_request(extra_params: dict) -> list:
            p = urllib.parse.urlencode({**base_params, **extra_params})
            c = http.client.HTTPSConnection(RAPIDAPI_HOST)
            c.request("GET", f"/api/v1/hotels/searchHotels?{p}", headers=headers)
            d = json.loads(c.getresponse().read().decode("utf-8"))
            return (d.get("data") or {}).get("hotels") or []

        # For premium requests, first try filtering to 4–5 star properties
        if budget_level == "premium":
            all_hotels_raw = _do_hotel_request({"categories_filter": "class::4,class::5"})
            if not all_hotels_raw:
                # Fall back to unfiltered if no premium results found
                all_hotels_raw = _do_hotel_request({})
        else:
            all_hotels_raw = _do_hotel_request({})

        data = {"status": True, "data": {"hotels": all_hotels_raw}}

        hotels = []
        if data.get("status") and data.get("data", {}).get("hotels"):
            all_hotels = data["data"]["hotels"]

            # Helper extractors to keep sort lambdas readable
            def _hotel_price(h):
                return h.get("property", {}).get("priceBreakdown", {}).get("grossPrice", {}).get("value", 0) or 0

            def _hotel_stars(h):
                return h.get("property", {}).get("propertyClass", 0) or 0

            if budget_level == "budget":
                # Sort cheapest first; drop hotels above 150 EUR/night to avoid noise
                candidates = [h for h in all_hotels if (_hotel_price(h) / max(nights, 1)) <= 150]
                if not candidates:
                    candidates = all_hotels
                candidates.sort(key=lambda h: _hotel_price(h))
            elif budget_level == "premium":
                # Sort by star rating, then price; drop anything under 80 EUR/night
                candidates = [h for h in all_hotels if (_hotel_price(h) / max(nights, 1)) >= 80]
                if not candidates:
                    candidates = all_hotels
                candidates.sort(key=lambda h: (_hotel_stars(h), _hotel_price(h)), reverse=True)
            else:  # mid
                # Exclude outliers above 1000 EUR/night; rank by review score
                candidates = [h for h in all_hotels if (_hotel_price(h) / max(nights, 1)) <= 1000]
                if not candidates:
                    candidates = all_hotels
                candidates = sorted(candidates, key=lambda h: h.get("property", {}).get("reviewScore", 0) or 0, reverse=True)

            for h in candidates[:9]:
                prop = h.get("property", {})
                total_price = prop.get("priceBreakdown", {}).get("grossPrice", {}).get("value", 0)
                price_per_night = round(total_price / max(nights, 1), 0) if total_price else None
                raw_photo = prop.get("photoUrls", [None])[0]
                photo_url = (
                    "/api/proxy-image?url=" + urllib.parse.quote(raw_photo, safe='')
                ) if raw_photo else None
                hotel_id = h.get("hotel_id") or prop.get("id")
                if hotel_id:
                    # Direct hotel page: dest_type=hotel + hotel's own ID
                    hotel_url = (
                        f"https://www.booking.com/searchresults.html"
                        f"?dest_id={hotel_id}&dest_type=hotel"
                        f"&checkin={checkin}&checkout={checkout}"
                        f"&group_adults=2&no_rooms=1&selected_currency=EUR"
                    )
                else:
                    # Fallback: search pre-filled with hotel name
                    hotel_url = (
                        f"https://www.booking.com/searchresults.html"
                        f"?ss={urllib.parse.quote(prop.get('name', ''))}"
                        f"&checkin={checkin}&checkout={checkout}"
                        f"&group_adults=2&no_rooms=1"
                    )
                hotels.append({
                    "name": prop.get("name", "Невідомий готель"),
                    "price_per_night": price_per_night,
                    "total_price": round(total_price, 0) if total_price else None,
                    "review_score": prop.get("reviewScore", 0),
                    "review_word": prop.get("reviewScoreWord", ""),
                    "stars": prop.get("propertyClass", 0),
                    "photo": photo_url,
                    "booking_url": hotel_url,
                })
            hotels.sort(key=lambda x: x.get("review_score", 0), reverse=True)
        return hotels[:6]
    except Exception:
        return []


@app.route("/")
def index():
    """Serve the single-page application shell."""
    return render_template("index.html")


# Pre-built demo route shown to unauthenticated users without an AI call
_DEMO_ROUTE = {
    "title": "Вічне місто: Рим за 3 дні",
    "tagline": "Колізей, тратторії та солодке дольче фар ньєнте",
    "destination": "Рим, Італія",
    "destination_city": "Rome",
    "duration": "3 дні",
    "nights": 2,
    "recommended_month": 4,
    "recommended_month_reason": "Квітень — ідеальний час: тепло, ще немає літніх натовпів туристів.",
    "estimated_budget": "350–500 EUR",
    "estimated_budget_total": 425,
    "best_season": "Навесні (березень–травень) та восени (вересень–жовтень)",
    "difficulty": "Легкий",
    "weather_summary": "Квітень у Римі — тепло (+18°C вдень), можливі короткі дощі ввечері.",
    "hotel_type": "3★ бутик-готель у центрі або Airbnb-апартаменти",
    "hotel_tips": "Шукайте готелі в районі Трастевере або поблизу Пантеону — найкраще розташування.",
    "days": [
        {
            "day": 1,
            "title": "Серце Стародавнього Риму",
            "location": "Колізей, Форум, Палатин",
            "lat": 41.8902, "lng": 12.4922,
            "morning": "Колізей і Форум Романум",
            "morning_place": "Colosseum Rome",
            "morning_lat": 41.8902, "morning_lng": 12.4922,
            "morning_detail": "Амфітеатр Флавіїв — найбільша арена стародавнього світу, місткістю до 80 000 глядачів. Підніміться на другий рівень для панорамного виду на арену та Форум.",
            "morning_tips": "Бронюйте квитки онлайн заздалегідь — черги можуть бути 2–3 години. Відкрито з 9:00, квиток (€18) включає Форум і Палатин.",
            "afternoon": "Палатинський пагорб і Велика тріумфальна арка",
            "afternoon_place": "Arch of Constantine Rome",
            "afternoon_lat": 41.8896, "afternoon_lng": 12.4908,
            "afternoon_detail": "Палатин — найдавніший із семи пагорбів Риму з приголомшливим виглядом на Circus Maximus. Арка Костянтина — одна з найкраще збережених тріумфальних арок античності.",
            "afternoon_tips": "Квиток включений у вхід до Колізею. Беріть воду — влітку дуже спекотно.",
            "evening": "Вечір у Трастевере",
            "evening_place": "Trastevere Rome",
            "evening_lat": 41.8896, "evening_lng": 12.4696,
            "evening_detail": "Богемний квартал із брукованими вуличками, квітковими вікнами та атмосферними тратторіями. Ввечері тут збирається вся молодь Риму.",
            "evening_tips": "Вечеряйте не раніше 20:00 — саме тоді відкриваються найкращі місця.",
            "food_tip": "Спробуйте сuppa di cacio e pepe — класична римська паста з чорним перцем і сиром пекоріно.",
            "estimated_cost": "120–150 EUR",
            "restaurants": [
                {
                    "name": "Trattoria da Enzo al 29",
                    "cuisine": "Italian Roman",
                    "cuisine_emoji": "🍝",
                    "price": "€€",
                    "description": "Культова траттерія у Трастевере з автентичною римською кухнею. Черги — ознака якості.",
                    "rating": 4.7,
                    "review": "Cacio e pepe тут — найкраща у Римі. Обов'язково бронюйте.",
                    "maps_query": "Trattoria da Enzo al 29 Rome",
                    "photo_query": "cozy Italian trattoria stone walls wooden tables Rome",
                    "lat": 41.8882, "lng": 12.4701
                },
                {
                    "name": "Pizzarium Bonci",
                    "cuisine": "Pizza al taglio",
                    "cuisine_emoji": "🍕",
                    "price": "€",
                    "description": "Найвідоміша піца al taglio у Римі від майстра Gabriele Bonci. Свіже тісто та незвичайні начинки.",
                    "rating": 4.6,
                    "review": "Краща pizza al taglio, яку я коли-небудь їв. Не пропустіть!",
                    "maps_query": "Pizzarium Bonci Rome",
                    "photo_query": "pizza al taglio Rome artisan bakery",
                    "lat": 41.9046, "lng": 12.4564
                }
            ]
        },
        {
            "day": 2,
            "title": "Ватикан і Borghese",
            "location": "Ватикан, вілла Боргезе",
            "lat": 41.9022, "lng": 12.4539,
            "morning": "Ватиканські музеї та Сикстинська капела",
            "morning_place": "Vatican Museums Rome",
            "morning_lat": 41.9065, "morning_lng": 12.4536,
            "morning_detail": "Найбільший музейний комплекс у світі — понад 70 000 творів мистецтва. Кульмінація — Сикстинська капела зі стелею Мікеланджело.",
            "morning_tips": "Бронюйте квитки за 2–3 тижні наперед (€20). Приходьте о 8:00 — найменше народу.",
            "afternoon": "Собор Святого Петра і площа",
            "afternoon_place": "St Peters Basilica Vatican",
            "afternoon_lat": 41.9022, "afternoon_lng": 12.4539,
            "afternoon_detail": "Найбільший католицький собор у світі вражає масштабом. Підніміться на купол (133 м) для панорами всього Риму — вид незабутній.",
            "afternoon_tips": "Вхід до базіліки безкоштовний, підйом на купол — €8 пішки або €10 на ліфті.",
            "evening": "Вілла Боргезе на заході сонця",
            "evening_place": "Villa Borghese Gardens Rome",
            "evening_lat": 41.9138, "evening_lng": 12.4922,
            "evening_detail": "Найкрасивіший парк Риму з приголомшливим виглядом на місто зі скелі Пінчо. Ідеальне місце для відпочинку після насиченого дня.",
            "evening_tips": "Галерея Боргезе всередині — обов'язкова, але квитки бронюються за кілька тижнів.",
            "food_tip": "Обід поблизу Ватикану — бери піцу al taglio, не сідай у туристичні ресторани на площі.",
            "estimated_cost": "90–120 EUR",
            "restaurants": [
                {
                    "name": "Osteria dell'Angelo",
                    "cuisine": "Roman",
                    "cuisine_emoji": "🍽",
                    "price": "€€",
                    "description": "Класична остерія поблизу Ватикану з фіксованим меню по обіді та автентичними римськими стравами.",
                    "rating": 4.5,
                    "review": "Аматріч'яна тут — вище всяких похвал. Дуже домашня атмосфера.",
                    "maps_query": "Osteria dell Angelo Rome",
                    "photo_query": "Roman osteria interior rustic cozy dinner",
                    "lat": 41.9036, "lng": 12.4571
                }
            ]
        },
        {
            "day": 3,
            "title": "Барокові фонтани та Campo de' Fiori",
            "location": "Центр, Пантеон, Пьяцца Навона",
            "lat": 41.8986, "lng": 12.4730,
            "morning": "Пантеон і площа Навона",
            "morning_place": "Pantheon Rome",
            "morning_lat": 41.8986, "morning_lng": 12.4769,
            "morning_detail": "Пантеон — найкраще збережена будівля стародавнього Риму (125 р. н.е.). Купол із неповторним окулусом досі не перевершений архітекторами. Площа Навона — серце Бароко з трьома фонтанами Берніні.",
            "morning_tips": "Вхід до Пантеону тепер платний — €5. Приходьте зранку, щоб уникнути черг.",
            "afternoon": "Фонтан Треві та Іспанські сходи",
            "afternoon_place": "Trevi Fountain Rome",
            "afternoon_lat": 41.9009, "afternoon_lng": 12.4833,
            "afternoon_detail": "Найвідоміший фонтан світу — киньте монету і загадайте бажання. Іспанські сходи — ідеальне місце для фотографій і споглядання міського життя.",
            "afternoon_tips": "На Фонтан Треві краще приходити рано вранці або пізно ввечері — менше туристів.",
            "evening": "Ринок Campo de' Fiori і вечеря",
            "evening_place": "Campo de Fiori Rome",
            "evening_lat": 41.8955, "evening_lng": 12.4723,
            "evening_detail": "Вранці тут живий ринок, ввечері — найжвавіший бар-квартал Риму. Безліч ресторанів, апетрифів та живої музики прямо під відкритим небом.",
            "evening_tips": "Спробуйте spritz або aperol на одній з терас — ідеальне завершення римської подорожі.",
            "food_tip": "Обов'язково спробуйте gelato в Giolitti — одна з найстаріших джелатерій Риму з 1900 року.",
            "estimated_cost": "80–100 EUR",
            "restaurants": [
                {
                    "name": "Ristorante Il Sorpasso",
                    "cuisine": "Modern Italian",
                    "cuisine_emoji": "🍷",
                    "price": "€€€",
                    "description": "Стильний ресторан із чудовим вибором вин і сучасною інтерпретацією класичних римських страв.",
                    "rating": 4.6,
                    "review": "Фінальна вечеря в Римі тут — ідеальна. Відмінний список вин.",
                    "maps_query": "Il Sorpasso Rome",
                    "photo_query": "modern Italian wine bar interior elegant Rome",
                    "lat": 41.9012, "lng": 12.4678
                }
            ]
        }
    ],
    "transport": {
        "origin": "Київ",
        "important_note": "Авіасполучення з українських аеропортів призупинено з лютого 2022. Виліт через Варшаву або Краків.",
        "local_transport": {
            "day_pass": "~7 EUR (48-годинний квиток)",
            "single_ride": "~1.5 EUR",
            "note": "Метро (лінії A та B) + автобуси. Для туристів зручніше ходити пішки між пам'ятками."
        },
        "routes": [
            {
                "label": "Автобус до Варшави + літак Ryanair",
                "recommended": True,
                "total_duration": "~12 год",
                "estimated_cost": "60–120 EUR",
                "summary": "Найдешевший варіант — Ryanair з Варшави до Рима Чампіно від €25.",
                "legs": [
                    {
                        "step": 1, "from": "Київ", "to": "Варшава",
                        "mode": "bus", "duration": "~8 год",
                        "note": "Ecolines або FlixBus, від 20 EUR",
                        "platforms": ["ecolines", "flixbus", "busfor"],
                        "links": []
                    },
                    {
                        "step": 2, "from": "Варшава", "to": "Рим",
                        "mode": "flight", "duration": "~2.5 год",
                        "note": "Ryanair або Wizz Air з WAW або WMI, від 25 EUR",
                        "platforms": ["ryanair", "wizzair", "kiwi"],
                        "links": []
                    }
                ]
            }
        ]
    },
    "practical_tips": [
        "Бронюйте Колізей, Ватикан і галерею Боргезе мінімум за 2–3 тижні наперед.",
        "Носіть зручне взуття — бруківка в центрі дуже нерівна.",
        "Вода з міських фонтанчиків (nasoni) — безпечна і смачна, набирайте безкоштовно.",
        "У базіліки вхід з покритими плечима та колінами — майте хустку або кардиган.",
        "Апертол-шпріц у барах обходиться вдвічі дешевше, ніж у ресторанах.",
    ],
    "hidden_gems": [
        "Базіліка Сан-Клементе — три рівні історії: сучасна, середньовічна та стародавня церква одна над одною.",
        "Пьяцца Белль'Арте — тихий артистичний квартал уздовж Тибру, де місцеві художники виставляють роботи.",
    ],
    "budget_detail": {
        "accommodation": {
            "price_per_night": 90,
            "nights": 2,
            "subtotal": 180,
            "note": "3★ готель у центрі або Airbnb"
        },
        "transport": {"subtotal": 90, "note": "Автобус Київ–Варшава + авіаквиток туди-назад"},
        "food": {"per_day": 35, "days": 3, "subtotal": 105, "note": "Кафе, тратторії, вуличний фуд"},
        "activities": {"subtotal": 55, "note": "Колізей €18 + Ватикан €20 + Пантеон €5 + інше"},
        "local_transport": {"subtotal": 15, "note": "48-год квиток × 2 + разові поїздки"},
        "misc": {"subtotal": 30, "note": "Джелато, сувеніри, дрібні витрати"},
        "total_min": 430,
        "total_max": 530
    }
}


@app.route("/api/demo-route")
def demo_route():
    """Return a pre-built demo route for unauthenticated users."""
    return jsonify({"success": True, "ai_route": _DEMO_ROUTE,
                    "hotels": [], "weather": None, "weather_type": "none",
                    "transport": _DEMO_ROUTE["transport"],
                    "budget_level": "mid", "similar_routes": [],
                    "hostel_url": None, "is_demo": True})


# ── AUTH ──

@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    """Register a new user account with email, name, and password."""
    data = request.json
    email = (data.get("email") or "").strip().lower()
    name  = (data.get("name")  or "").strip()
    pwd   = data.get("password", "")
    if not email or not name or not pwd:
        return jsonify({"success": False, "error": "Заповніть усі поля"}), 400
    if len(pwd) < 8:
        return jsonify({"success": False, "error": "Пароль мінімум 8 символів"}), 400
    try:
        with _get_db() as db:
            db.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
                (email, name, generate_password_hash(pwd))
            )
            db.commit()
            user = db.execute("SELECT id, email, name FROM users WHERE email=?", (email,)).fetchone()
            session["user_id"] = user["id"]
            return jsonify({"success": True, "user": {
                "id": user["id"], "email": user["email"], "name": user["name"], "interests": [],
            }})
    except psycopg2.errors.UniqueViolation:
        return jsonify({"success": False, "error": "Цей email вже зареєстровано"}), 409


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """Authenticate user credentials and create a session."""
    data  = request.json
    email = (data.get("email") or "").strip().lower()
    pwd   = data.get("password", "")
    with _get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], pwd):
        return jsonify({"success": False, "error": "Невірний email або пароль"}), 401
    session["user_id"] = user["id"]
    return jsonify({"success": True, "user": {
        "id": user["id"], "email": user["email"], "name": user["name"],
        "interests": json.loads(user["interests"] or "[]"),
    }})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    """Destroy the current user session."""
    session.pop("user_id", None)
    return jsonify({"success": True})


@app.route("/api/auth/me")
def auth_me():
    """Return the currently logged-in user's profile, or null if unauthenticated."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"user": None})
    with _get_db() as db:
        user = db.execute("SELECT id, email, name, interests FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        session.pop("user_id", None)
        return jsonify({"user": None})
    return jsonify({"user": {
        "id": user["id"], "email": user["email"], "name": user["name"],
        "interests": json.loads(user["interests"] or "[]"),
    }})


@app.route("/api/auth/save-interests", methods=["POST"])
def auth_save_interests():
    """Save user interests from onboarding and return personalized route recommendations."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "error": "Не авторизовано"}), 401
    data = request.json or {}
    interests = data.get("interests", [])
    if not isinstance(interests, list):
        interests = []
    interests = [str(i).strip().lower() for i in interests if i][:20]
    with _get_db() as db:
        db.execute("UPDATE users SET interests=? WHERE id=?", (json.dumps(interests), uid))
        db.commit()
    recs = get_recommendations(uid, interests, "mid", limit=6)
    return jsonify({"success": True, "recommendations": recs})


@app.route("/api/auth/forgot-password", methods=["POST"])
def auth_forgot_password():
    """Generate a one-hour password-reset token and email it to the user."""
    email = (request.json.get("email") or "").strip().lower()
    if not email:
        return jsonify({"success": False, "error": "Введіть email"}), 400
    with _get_db() as db:
        user = db.execute("SELECT id, name FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        # Don't reveal if email exists
        return jsonify({"success": True, "message": "Якщо цей email зареєстровано — лист надіслано"})
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with _get_db() as db:
        db.execute("DELETE FROM password_resets WHERE user_id=?", (user["id"],))
        db.execute("INSERT INTO password_resets (user_id, token, expires_at) VALUES (?,?,?)",
                   (user["id"], token, expires))
        db.commit()
    reset_url = f"{APP_URL}/?reset_token={token}"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:2rem;background:#111;color:#f5f0e8;">
      <h2 style="color:#c4a052;letter-spacing:0.3em;font-weight:300;">WANDR</h2>
      <p>Привіт, {user['name']}!</p>
      <p>Ти запросив відновлення пароля. Натисни кнопку нижче — посилання дійсне 1 годину.</p>
      <a href="{reset_url}" style="display:inline-block;margin:1.5rem 0;padding:0.8rem 2rem;background:#c45c3a;color:#fff;text-decoration:none;font-family:monospace;letter-spacing:0.2em;text-transform:uppercase;">
        Відновити пароль →
      </a>
      <p style="font-size:0.8rem;color:#888;">Якщо ти не запитував — просто ігноруй цей лист.</p>
    </div>"""
    sent = _send_email(email, "Wandr — відновлення пароля", html)
    if not sent:
        # Dev mode: return token directly so it can be tested without SMTP
        return jsonify({"success": True, "message": "Якщо цей email зареєстровано — лист надіслано", "_dev_token": token})
    return jsonify({"success": True, "message": "Якщо цей email зареєстровано — лист надіслано"})


@app.route("/api/auth/reset-password", methods=["POST"])
def auth_reset_password():
    """Validate the reset token, update the password, and log the user in."""
    token   = (request.json.get("token") or "").strip()
    new_pwd = request.json.get("password", "")
    if not token or not new_pwd:
        return jsonify({"success": False, "error": "Невірні дані"}), 400
    if len(new_pwd) < 8:
        return jsonify({"success": False, "error": "Пароль мінімум 8 символів"}), 400
    with _get_db() as db:
        row = db.execute(
            "SELECT * FROM password_resets WHERE token=? AND used=0", (token,)
        ).fetchone()
        if not row:
            return jsonify({"success": False, "error": "Посилання недійсне або вже використано"}), 400
        if datetime.now() > datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S"):
            return jsonify({"success": False, "error": "Посилання застаріло — запросіть нове"}), 400
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (generate_password_hash(new_pwd), row["user_id"]))
        db.execute("UPDATE password_resets SET used=1 WHERE token=?", (token,))
        db.commit()
        user = db.execute("SELECT id, email, name FROM users WHERE id=?", (row["user_id"],)).fetchone()
        session["user_id"] = user["id"]
    return jsonify({"success": True, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}})


# ── SAVED ROUTES ──

@app.route("/api/routes/save", methods=["POST"])
def routes_save():
    """Persist a generated route with its interests and budget_level to the database."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "error": "Потрібна авторизація"}), 401
    data  = request.json
    route = data.get("route", {})
    interests    = data.get("interests", [])
    budget_level = data.get("budget_level", "mid")
    with _get_db() as db:
        db.execute(
            "INSERT INTO saved_routes (user_id, title, destination, duration, route_data, interests, budget_level) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, route.get("title", "Маршрут"), route.get("destination", ""), route.get("duration", ""),
             json.dumps(route, ensure_ascii=False), json.dumps(interests), budget_level)
        )
        db.commit()
    return jsonify({"success": True})


@app.route("/api/routes/saved")
def routes_saved():
    """List all saved routes for the authenticated user (metadata only, no route_data)."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "error": "Потрібна авторизація"}), 401
    with _get_db() as db:
        rows = db.execute(
            "SELECT id, title, destination, duration, created_at FROM saved_routes WHERE user_id=? ORDER BY created_at DESC",
            (uid,)
        ).fetchall()
    return jsonify({"success": True, "routes": [dict(r) for r in rows]})


@app.route("/api/routes/saved/<int:route_id>", methods=["GET"])
def routes_saved_get(route_id):
    """Return the full route_data JSON for a specific saved route owned by the user."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "error": "Потрібна авторизація"}), 401
    with _get_db() as db:
        row = db.execute("SELECT * FROM saved_routes WHERE id=? AND user_id=?", (route_id, uid)).fetchone()
    if not row:
        return jsonify({"success": False, "error": "Не знайдено"}), 404
    return jsonify({"success": True, "route": json.loads(row["route_data"])})


@app.route("/api/routes/saved/<int:route_id>", methods=["DELETE"])
def routes_saved_delete(route_id):
    """Delete a saved route that belongs to the authenticated user."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "error": "Потрібна авторизація"}), 401
    with _get_db() as db:
        db.execute("DELETE FROM saved_routes WHERE id=? AND user_id=?", (route_id, uid))
        db.commit()
    return jsonify({"success": True})


@app.route("/api/routes/preview/<int:route_id>")
def routes_preview(route_id):
    """Public endpoint — returns stored route data for recommended route preview."""
    with _get_db() as db:
        row = db.execute(
            "SELECT title, destination, duration, route_data, interests, budget_level FROM saved_routes WHERE id=?",
            (route_id,)
        ).fetchone()
    if not row:
        return jsonify({"success": False, "error": "Не знайдено"}), 404
    try:
        route_data = json.loads(row["route_data"])
    except Exception:
        route_data = {}
    return jsonify({
        "success": True,
        "route": route_data,
        "title": row["title"],
        "destination": row["destination"],
        "duration": row["duration"],
        "budget_level": row["budget_level"],
        "interests": json.loads(row["interests"] or "[]"),
    })


@app.route("/api/reviews/<int:route_id>")
def reviews_get(route_id):
    """Public endpoint — returns reviews for a route."""
    with _get_db() as db:
        rows = db.execute("""
            SELECT rv.rating, rv.comment, rv.created_at, u.name
            FROM reviews rv
            JOIN users u ON u.id = rv.user_id
            WHERE rv.route_id = ?
            ORDER BY rv.created_at DESC
            LIMIT 20
        """, (route_id,)).fetchall()
    reviews = []
    for r in rows:
        name_parts = (r["name"] or "").split()
        display_name = name_parts[0] if name_parts else "Мандрівник"
        reviews.append({
            "rating":  r["rating"],
            "comment": r["comment"] or "",
            "date":    (r["created_at"] or "")[:10],
            "author":  display_name,
        })
    avg = round(sum(r["rating"] for r in reviews) / len(reviews), 1) if reviews else None
    return jsonify({"success": True, "reviews": reviews, "avg_rating": avg, "total": len(reviews)})


@app.route("/api/reviews/add", methods=["POST"])
def reviews_add():
    """Add or replace the authenticated user's review (rating + comment) for a route."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "error": "Потрібна авторизація"}), 401
    data = request.json or {}
    route_id = data.get("route_id")
    rating   = data.get("rating")
    comment  = data.get("comment", "")
    if not route_id or not rating:
        return jsonify({"success": False, "error": "Невірні дані"}), 400
    try:
        rating = int(rating)
        if not (1 <= rating <= 5):
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Рейтинг від 1 до 5"}), 400
    with _get_db() as db:
        db.execute(
            "INSERT INTO reviews (user_id, route_id, rating, comment) VALUES (?,?,?,?)"
            " ON CONFLICT (user_id, route_id)"
            " DO UPDATE SET rating=EXCLUDED.rating, comment=EXCLUDED.comment",
            (uid, route_id, rating, comment)
        )
        db.commit()
    return jsonify({"success": True})


@app.route("/api/routes/similar", methods=["POST"])
def routes_similar():
    """Return recommended routes from DB based on interests and budget_level."""
    data = request.json or {}
    interests    = data.get("interests", [])
    budget_level = data.get("budget_level", "mid")
    uid = session.get("user_id")
    recs = get_recommendations(uid, interests, budget_level, limit=6)
    return jsonify({"success": True, "routes": recs})


@app.route("/api/suggest-dates", methods=["POST"])
def suggest_dates():
    """Quick AI call: given weather prefs, suggest destination + best month + coordinates."""
    data = request.json or {}
    destination  = data.get("destination", "")
    weather_pref = data.get("weather_pref", "")
    departure_city = data.get("departure_city", "")
    duration     = data.get("duration", "")

    prompt = f"""You are a travel advisor. Based on the preferences below, suggest the best travel destination and the ideal month to visit it.

Departure city: {departure_city or 'flexible'}
Destination hint: {destination or 'flexible — suggest something interesting'}
Trip duration: {duration or 'flexible'}
Weather preference: {weather_pref or 'pleasant weather'}

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "city": "City name in English",
  "city_ua": "City name in Ukrainian",
  "country_ua": "Country name in Ukrainian",
  "lat": 0.0,
  "lng": 0.0,
  "recommended_month": 6,
  "month_name": "Червень",
  "typical_weather": "Short Ukrainian description of weather in that month (1-2 sentences)"
}}"""

    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.4,
        )
        suggestion = json.loads(resp.choices[0].message.content)
        return jsonify({"success": True, **suggestion})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── ROUTE GENERATION HELPERS ──

# Budget level instructions injected into the AI prompt
_BUDGET_LEVEL_GUIDE = {
    "budget": (
        "БЮДЖЕТНИЙ рівень. Підбирай рекомендації відповідно:\n"
        "- Житло: хостели, капсульні готелі, Airbnb-кімнати, 1-2★ готелі (~20-50 EUR/ніч)\n"
        "- Харчування: вуличний фуд, ринки, супермаркети, місцеві кафе без алкоголю (~10-20 EUR/день)\n"
        "- Активності: безкоштовні музеї, парки, пішохідні екскурсії, оглядові майданчики\n"
        "- Транспорт: автобус, метро, трамвай, велопрокат\n"
        "- Ресторани: ціна €-€€, місцева кухня, вуличний фуд"
    ),
    "mid": (
        "КОМФОРТ рівень. Підбирай рекомендації відповідно:\n"
        "- Житло: 3★ готелі, бутик-готелі, Airbnb-апартаменти (~60-120 EUR/ніч)\n"
        "- Харчування: місцеві ресторани, бістро, брасері (~30-50 EUR/день)\n"
        "- Активності: платні музеї та атракції, екскурсії з гідом, дегустації\n"
        "- Транспорт: поїзд, таксі, оренда авто\n"
        "- Ресторани: ціна €€-€€€, хороший вибір страв і вин"
    ),
    "premium": (
        "ПРЕМІУМ рівень. Підбирай рекомендації відповідно:\n"
        "- Житло: 4-5★ готелі, design-готелі, люкс-апартаменти (~150-500+ EUR/ніч)\n"
        "- Харчування: fine dining, ресторани з зірками Мішлен, авторська кухня (~80-200+ EUR/день)\n"
        "- Активності: приватні тури, VIP-доступ, дегустації преміум-вин, спа, гелікоптерні прогулянки\n"
        "- Транспорт: приватні трансфери, бізнес-клас, оренда авто преміум-класу\n"
        "- Ресторани: ціна €€€€, знакові та найкращі заклади міста"
    ),
}


def _get_budget_guide(budget_level: str) -> str:
    """Return the AI-prompt budget instructions for the given budget level."""
    return _BUDGET_LEVEL_GUIDE.get(budget_level, _BUDGET_LEVEL_GUIDE["mid"])


def _build_people_str(num_people: int, num_children: int) -> str:
    """Format traveller count as a human-readable Ukrainian string."""
    word = 'мандрівник' if num_people == 1 else 'мандрівники' if num_people < 5 else 'мандрівників'
    result = f"{num_people} {word}"
    if num_children:
        ch_word = 'дитина' if num_children == 1 else 'дитини' if num_children < 5 else 'дітей'
        result += f" + {num_children} {ch_word}"
    return result


def _build_special_context(has_children: bool, has_a11y: bool, num_children: int) -> str:
    """Build extra AI-prompt instructions for trips with children or accessibility needs."""
    ctx = ""
    if has_children:
        ctx += (
            f"\n⚠️ ВАЖЛИВО — ПОДОРОЖ З ДІТЬМИ ({num_children or 'є'} дітей): додай у кожен день принаймні одне місце, "
            "яке буде цікавим саме дитині (парк атракціонів, зоопарк, інтерактивний музей, "
            "пляж з дрібним входом у море, дитячий майданчик у парку тощо). "
            "Враховуй комфорт дітей: короткі переходи, перерви, не надто рано/пізно. "
            "У budget_detail враховуй дитячі квитки (зазвичай 50% від дорослого або безкоштовно до 6 років)."
        )
    if has_a11y:
        ctx += (
            "\n⚠️ ВАЖЛИВО — ОСОБЛИВІ ПОТРЕБИ: підбирай тільки локації з доступністю для людей "
            "з обмеженими можливостями (пандуси, ліфти, відсутність довгих пішохідних маршрутів). "
            "Уникай місць без доступу для маломобільних людей."
        )
    return ctx


def _build_weather_context(weather_pref: str, checkin_date: str, checkout_date: str) -> str:
    """Build the weather/dates block for the AI prompt."""
    ctx = ""
    if weather_pref:
        ctx = f"\n- Побажання щодо погоди: {weather_pref}"
    if checkin_date:
        ctx += f"\n- Дати подорожі: {checkin_date} — {checkout_date}"
    return ctx


def _build_ukraine_transport_note(departure_city: str) -> str:
    """Return the Ukraine airspace-closure warning when departing from Ukraine."""
    # Ukrainian airports have been closed since Feb 2022 — the AI must route via neighbouring countries
    if not (departure_city and _is_ukraine(departure_city)):
        return ""
    return (
        "\nКРИТИЧНО ВАЖЛИВО для транспорту: авіасполучення з аеропортів УКРАЇНИ ПОВНІСТЮ ПРИПИНЕНО з лютого 2022 через воєнний стан. "
        "Жодних прямих рейсів з Києва, Харкова, Одеси та інших українських аеропортів НЕ ІСНУЄ. Тому:\n"
        "- Маршрут через летовище можливий лише якщо спочатку дістатися до аеропорту сусідньої країни наземним транспортом\n"
        "- Популярні аеропорти для вильоту: Варшава (WAW/WMI), Краків (KRK), Будапешт (BUD), Бухарест (OTP), Кишинів (KIV), Братислава (BTS), Жешув (RZE)\n"
        "- Для переїзду до кордону: поїзд Укрзалізниці або автобус (Ecolines, FlixBus, Busfor)\n"
        "- Запропонуй 3-4 різних варіанти з конкретними пересадковими містами"
    )


def _determine_weather_data(lat, lng, checkin_date: str, checkout_date: str, recommended_month: int):
    """Decide whether to fetch a real forecast or climate normals, and return (data, type)."""
    # No coordinates — skip weather entirely
    if not (lat and lng):
        return None, "none"
    if checkin_date and checkout_date:
        try:
            checkin_dt = datetime.strptime(checkin_date, "%Y-%m-%d")
            # Open-Meteo forecast covers only 16 days ahead; fall back to climate for later dates
            if checkin_dt <= datetime.now() + timedelta(days=16):
                return get_weather_forecast(lat, lng, checkin_date, checkout_date), "forecast"
            return get_climate_normals(lat, lng, checkin_dt.month), "climate_dates"
        except Exception:
            return None, "none"
    # No dates supplied — show typical climate for the AI-recommended month
    return get_climate_normals(lat, lng, recommended_month), "climate_recommended"


def _finalize_budget(bd: dict, hotels: list, num_people: int, num_children: int, nights: int) -> None:
    """Overwrite accommodation with real Booking.com prices and recalculate budget totals."""
    # Replace AI-estimated accommodation prices with live Booking.com data when available
    if hotels:
        real_prices = [h["price_per_night"] for h in hotels if h.get("price_per_night")]
        if real_prices:
            real_avg = round(sum(real_prices) / len(real_prices))
            acc = bd.setdefault("accommodation", {})
            acc["price_per_night"] = real_avg
            acc["nights"]          = nights
            acc["subtotal"]        = real_avg * nights
            acc["note"]            = f"Реальні ціни Booking.com: від {min(real_prices)} до {max(real_prices)} EUR/ніч"

    # Guard against AI calculating costs for 1 person when multiple people travel
    if num_people > 1:
        for cat_key in ("food", "activities", "local_transport"):
            cat = bd.get(cat_key)
            if not cat:
                continue
            sub     = cat.get("subtotal") or 0
            per_day = cat.get("per_day")
            days_val = cat.get("days")
            if per_day and days_val:
                expected_single = per_day * days_val
                # Scale up only when the AI clearly forgot to multiply by num_people
                if abs(sub - expected_single) < expected_single * 0.15:
                    cat["subtotal"] = round(sub * num_people)

    # Recalculate the total budget range from the individual category subtotals
    cats = ["accommodation", "transport", "food", "activities", "local_transport", "misc"]
    total = sum((bd.get(c) or {}).get("subtotal", 0) for c in cats)
    bd["total_min"] = round(total * 0.9)
    bd["total_max"] = round(total * 1.15)


def _build_hostel_url(dest_city: str, nights: int, checkin_date: str, checkout_date: str) -> str:
    """Build a Booking.com hostel-only search URL (ht_id=203) for the given city and dates."""
    ci = checkin_date or (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    co = checkout_date or (datetime.now() + timedelta(days=30 + max(nights, 1))).strftime("%Y-%m-%d")
    qs = urllib.parse.urlencode({
        "ss": dest_city, "ht_id": "203",
        "checkin": ci, "checkout": co,
        "group_adults": "1", "no_rooms": "1",
        "selected_currency": "EUR",
    })
    return f"https://www.booking.com/searchresults.html?{qs}"


@app.route("/api/generate-route", methods=["POST"])
def generate_route():
    """Generate a personalised AI travel route with weather, hotels, and transport options."""
    data = request.json
    budget        = data.get("budget", 0)
    budget_level  = data.get("budget_level", "mid")
    interests     = data.get("interests", [])
    destination   = data.get("destination", "")
    departure_city = data.get("departure_city", "")
    duration      = data.get("duration", "")
    extra_notes   = data.get("extra_notes", "")
    num_people    = max(1, int(data.get("num_people",   1) or 1))
    num_children  = max(0, int(data.get("num_children", 0) or 0))
    checkin_date  = data.get("checkin_date", "")
    checkout_date = data.get("checkout_date", "")
    weather_pref  = data.get("weather_pref", "")

    # Detect special traveller contexts from num_children field or keywords in notes
    notes_lower  = (extra_notes or "").lower()
    has_children = num_children > 0 or any(w in notes_lower for w in ["дитин", "дітей", "дитя", "малюк", "child", "kid", "baby"])
    has_a11y     = any(w in notes_lower for w in ["інвалід", "візок", "колясц", "accessibility", "wheelchair", "особлив"])

    # Build reusable context strings via helpers
    people_str      = _build_people_str(num_people, num_children)
    budget_level_str = _get_budget_guide(budget_level)
    special_context = _build_special_context(has_children, has_a11y, num_children)
    weather_context = _build_weather_context(weather_pref, checkin_date, checkout_date)
    ukraine_transport_note = _build_ukraine_transport_note(departure_city)

    # Fetch similar routes from DB for the recommendation context block
    uid          = session.get("user_id")
    filtered     = get_recommendations(uid, interests, budget_level, limit=5)
    interests_str = ", ".join(interests) if interests else "різноманітні"
    filtered_str = json.dumps(
        [{"title": r["title"], "destination": r["destination"],
          "avg_rating": r["avg_rating"], "review_count": r["review_count"]} for r in filtered[:3]],
        ensure_ascii=False
    ) if filtered else "[]"

    prompt = f"""Ти — досвідчений тревел-консультант. Створи персоналізований туристичний маршрут.

Параметри мандрівника:
- Рівень бюджету: {budget_level_str}
- Кількість мандрівників: {people_str}
- Загальний бюджет: {budget} EUR {'(не вказано)' if not budget else ''}
- Інтереси: {interests_str}
- Місто відправлення: {departure_city if departure_city else 'не вказано'}
- Бажаний напрямок: {destination if destination else 'будь-який'}
- Тривалість: {duration if duration else 'гнучка'}{weather_context}
- Додаткові побажання: {extra_notes if extra_notes else 'немає'}{special_context}

Схожі маршрути з бази: {filtered_str}

Створи детальний маршрут у JSON форматі (ТІЛЬКИ JSON, без markdown та коментарів):
{{
  "title": "Назва маршруту",
  "tagline": "Короткий слоган",
  "destination": "Місто/Країна",
  "destination_city": "Назва міста англійською для пошуку готелів (наприклад: Barcelona)",
  "duration": "X днів",
  "nights": 5,
  "recommended_month": 6,
  "recommended_month_reason": "Чому цей місяць найкращий для цього маршруту",
  "estimated_budget": "XXX-XXX EUR",
  "estimated_budget_total": 850,
  "best_season": "Найкращий сезон",
  "difficulty": "Легкий/Середній/Складний",
  "weather_summary": "Короткий опис типової погоди для цієї подорожі",
  "hotel_type": "Тип житла відповідно до рівня бюджету (наприклад: хостел у центрі, бутик-готель 3★, розкішний готель 5★)",
  "hotel_tips": "Порада де шукати та бронювати житло для цього рівня бюджету",
  "days": [
    {{
      "day": 1,
      "title": "Назва дня",
      "location": "Конкретна локація дня",
      "lat": 48.8566,
      "lng": 2.3522,
      "morning": "Коротка назва ранкової активності (1 речення)",
      "morning_place": "Точна назва місця/пам'ятки англійською для пошуку фото",
      "morning_lat": 48.8606,
      "morning_lng": 2.3376,
      "morning_detail": "2-3 речення: що саме тут можна побачити, яка атмосфера, унікальні деталі",
      "morning_tips": "Практична порада: години роботи, ціна квитка, найкращий час, що не пропустити",
      "afternoon": "Коротка назва денної активності (1 речення)",
      "afternoon_place": "Точна назва місця англійською",
      "afternoon_lat": 48.8530,
      "afternoon_lng": 2.3499,
      "afternoon_detail": "2-3 речення: що побачити, атмосфера, унікальні деталі",
      "afternoon_tips": "Практична порада: години, ціна, що не пропустити",
      "evening": "Коротка назва вечірньої активності (1 речення)",
      "evening_place": "Точна назва місця англійською",
      "evening_lat": 48.8584,
      "evening_lng": 2.2945,
      "evening_detail": "2-3 речення: вечірня атмосфера, що робити, куди піти",
      "evening_tips": "Практична порада для вечора",
      "food_tip": "Порада про їжу",
      "estimated_cost": "XX EUR",
      "restaurants": [
        {{
          "name": "Назва ресторану",
          "cuisine": "Тип кухні",
          "cuisine_emoji": "🍝",
          "price": "€€",
          "description": "Короткий опис ресторану (1-2 речення)",
          "rating": 4.5,
          "review": "Короткий реальний відгук відвідувача",
          "maps_query": "Назва ресторану Місто",
          "photo_query": "cozy Italian trattoria stone walls wooden tables candles warm lighting",
          "lat": 41.8902,
          "lng": 12.4922
        }}
      ]
    }}
  ],
  "transport": {{
    "important_note": "Якщо є обмеження (закрите небо, прикордонні обмеження тощо) — вкажи тут",
    "local_transport": {{
      "day_pass": "~X EUR (якщо є денний/добовий квиток — вкажи ціну, інакше null)",
      "single_ride": "~X EUR",
      "note": "Коротко: як краще пересуватися містом (метро, автобус, трамвай, тощо)"
    }},
    "routes": [
      {{
        "label": "Назва варіанту (напр. 'Автобус до Варшави + Літак')",
        "recommended": true,
        "total_duration": "~14 год",
        "estimated_cost": "80-150 EUR",
        "summary": "Чому цей варіант оптимальний",
        "legs": [
          {{
            "step": 1,
            "from": "Київ",
            "to": "Варшава",
            "mode": "bus",
            "duration": "~8 год",
            "note": "FlixBus або Ecolines, від 20 EUR",
            "platforms": ["flixbus", "ecolines", "busfor"]
          }},
          {{
            "step": 2,
            "from": "Варшава",
            "to": "Рим",
            "mode": "flight",
            "duration": "~2.5 год",
            "note": "Wizz Air або Ryanair, від 30-80 EUR",
            "platforms": ["kiwi", "google_flights", "wizzair", "ryanair"]
          }}
        ]
      }}
    ]
  }},
  "practical_tips": ["порада 1", "порада 2", "порада 3"],
  "hidden_gems": ["місце 1", "місце 2"],
  "budget_detail": {{
    "accommodation": {{
      "price_per_night": 60,
      "nights": 5,
      "subtotal": 300,
      "note": "Хостел у центрі / готель 3★ — реальна ринкова ціна"
    }},
    "transport": {{
      "subtotal": 140,
      "note": "Квитки туди-назад: середня ціна рекомендованого варіанту"
    }},
    "food": {{
      "per_day": 35,
      "days": 5,
      "subtotal": 175,
      "note": "Кафе, ринки, 1 ресторан на день — середній чек"
    }},
    "activities": {{
      "subtotal": 65,
      "note": "Вхідні квитки: перерахуй реальні ціни конкретних місць з маршруту"
    }},
    "local_transport": {{
      "subtotal": 20,
      "note": "Проїзд по місту: денний квиток або разові поїздки × кількість днів"
    }},
    "misc": {{
      "subtotal": 40,
      "note": "Сувеніри, напої, дрібні витрати (~10% від решти)"
    }},
    "total_min": 700,
    "total_max": 820
  }}
}}

ВАЖЛИВО:
- Для кожного дня вкажи точні реальні координати lat/lng та 2-3 конкретні реальні ресторани.
- У transport.local_transport вкажи реальну вартість проїзду в місці призначення: денний/добовий квиток (якщо є) та разовий квиток. Якщо денного квитка немає — day_pass: null.
- У transport.routes запропонуй 2-4 РІЗНИХ варіанти маршруту з покроковими пересадками (legs).
- У budget_detail розрахуй РЕАЛЬНИЙ бюджет на {people_str} з конкретними числами:
  * accommodation.price_per_night — реальна ринкова ціна за ніч за 1 номер для заданого рівня в цьому місті
  * accommodation.nights — кількість ночей (duration - 1)
  * accommodation.subtotal — price_per_night × nights (проживання НЕ множиться на кількість людей — ціна за кімнату)
  * transport.subtotal — вартість квитків туди-назад на ВСІХ ({num_people} дорослих{f' + {num_children} дітей' if num_children else ''}): recommended маршрут × 2 напрямки × {num_people}{f' + дитячі квитки × {num_children}' if num_children else ''}
  * transport.note — обов'язково вкажи "Квитки туди-назад на {people_str}"
  * food.per_day — реальний денний бюджет на їжу НА ОДНУ ЛЮДИНУ: budget=15-25, mid=30-55, premium=60-120 EUR
  * food.subtotal — per_day × кількість днів × {num_people + num_children} (на всіх, включно з дітьми)
  * activities.subtotal — РЕАЛЬНІ вхідні квитки × {num_people} дорослих{f' + дитячі × {num_children} (зазвичай 50% або безкоштовно)' if num_children else ''}
  * local_transport.subtotal — вартість проїзду по місту × {num_people + num_children} осіб на всі дні
  * misc.subtotal — ~10% від суми всіх інших категорій
  * total_min і total_max — реалістичний діапазон загального бюджету на {people_str}
  * estimated_budget_total — середнє значення між total_min та total_max (ціле число)
- Для кожного варіанту вказуй конкретні міста пересадок, час, вартість і платформи для пошуку.
- Допустимі значення platforms: google_flights, kiwi, skyscanner, wizzair, ryanair, easyjet, trainline, raileurope, omio, uz, flixbus, blablabus, busfor, ecolines, infobus, rome2rio, directferries, ferryscanner
{ukraine_transport_note}"""

    result_holder = {}

    def _do_work():
        try:
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
            message = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=3500,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Відповідай ТІЛЬКИ валідним JSON. Без markdown, без коментарів, без пояснень."},
                    {"role": "user", "content": prompt}
                ]
            )

            response_text = message.choices[0].message.content.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            ai_route = json.loads(response_text.strip())

            days              = ai_route.get("days", [])
            lat               = days[0].get("lat") if days else None
            lng               = days[0].get("lng") if days else None
            nights            = ai_route.get("nights", 3)
            recommended_month = ai_route.get("recommended_month", datetime.now().month)

            weather_data, weather_type = _determine_weather_data(
                lat, lng, checkin_date, checkout_date, recommended_month
            )

            dest_city = ai_route.get("destination_city") or ai_route.get("destination", destination)
            hotels = search_hotels(dest_city, budget, nights, checkin_date or None, checkout_date or None, budget_level) \
                     if (checkin_date and checkout_date) else []

            bd = ai_route.setdefault("budget_detail", {})
            _finalize_budget(bd, hotels, num_people, num_children, nights)

            ai_transport = ai_route.get("transport", {})
            ai_transport["origin"] = departure_city
            ai_transport["routes"] = enrich_transport_routes(ai_transport.get("routes", []))

            hostel_url = _build_hostel_url(dest_city, nights, checkin_date, checkout_date) \
                         if budget_level == "budget" else None

            result_holder["result"] = {
                "success": True,
                "ai_route": ai_route,
                "similar_routes": filtered[:3],
                "hotels": hotels,
                "weather": weather_data,
                "weather_type": weather_type,
                "transport": ai_transport,
                "budget_level": budget_level,
                "hostel_url": hostel_url,
            }
        except Exception as e:
            import traceback; traceback.print_exc()
            result_holder["error"] = str(e)

    t = threading.Thread(target=_do_work)
    t.start()

    def _stream():
        while t.is_alive():
            yield f"data: {json.dumps({'status': 'generating'})}\n\n"
            t.join(timeout=3)
        if "error" in result_holder:
            yield f"data: {json.dumps({'success': False, 'error': result_holder['error']})}\n\n"
        else:
            yield f"data: {json.dumps(result_holder.get('result', {'success': False, 'error': 'Unknown error'}))}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(_stream()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/weather-month", methods=["POST"])
def weather_month():
    """Return day-by-day historical climate averages for a given month and location."""
    data = request.json
    lat = data.get("lat")
    lng = data.get("lng")
    month = data.get("month")
    year = data.get("year")

    if not all([lat, lng, month, year]):
        return jsonify({"success": False, "error": "Missing params"}), 400

    try:
        import calendar
        from collections import Counter
        last_day = calendar.monthrange(year, month)[1]

        # Always use archive data from past 3 years to show historical climate patterns.
        # The forecast API only covers 16 days ahead and can't fill a full future month.
        all_days = {}
        current_year = datetime.now().year
        for past_year in range(current_year - 3, current_year):
            py_start = f"{past_year}-{month:02d}-01"
            py_end   = f"{past_year}-{month:02d}-{last_day}"
            past_days = get_weather_forecast_archive(lat, lng, py_start, py_end)
            for d in past_days:
                day_num = int(d["date"].split("-")[2])
                if day_num not in all_days:
                    all_days[day_num] = []
                all_days[day_num].append(d)

        days = []
        for day_num in range(1, last_day + 1):
            date_str = f"{year}-{month:02d}-{day_num:02d}"
            if day_num in all_days:
                records = all_days[day_num]
                valid_max    = [r["temp_max"] for r in records if r.get("temp_max") is not None]
                valid_min    = [r["temp_min"] for r in records if r.get("temp_min") is not None]
                valid_precip = [r["precipitation"] for r in records if r.get("precipitation") is not None]
                avg_max   = round(sum(valid_max) / len(valid_max), 1)       if valid_max    else None
                avg_min   = round(sum(valid_min) / len(valid_min), 1)       if valid_min    else None
                avg_precip= round(sum(valid_precip) / len(valid_precip), 1) if valid_precip else 0
                common_icon = Counter(r["icon"] for r in records if r.get("icon")).most_common(1)[0][0] if records else "🌡️"
                common_desc = Counter(r["description"] for r in records if r.get("description")).most_common(1)[0][0] if records else "—"
                days.append({
                    "date": date_str,
                    "temp_max": avg_max,
                    "temp_min": avg_min,
                    "precipitation": avg_precip,
                    "description": common_desc,
                    "icon": common_icon,
                    "type": "climate_avg"
                })
            else:
                days.append({
                    "date": date_str,
                    "temp_max": None, "temp_min": None,
                    "precipitation": 0, "description": "—", "icon": "🌡️",
                    "type": "unknown"
                })

        return jsonify({"success": True, "days": days})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

def get_weather_forecast_archive(lat, lng, start_date, end_date):
    """Same as forecast but uses archive API"""
    try:
        params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lng,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode,windspeed_10m_max",
            "timezone": "auto",
            "start_date": start_date,
            "end_date": end_date,
        })
        url = f"https://archive-api.open-meteo.com/v1/archive?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "WandrApp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        daily = data.get("daily", {})
        dates = daily.get("time", [])
        result = []
        for i, date in enumerate(dates):
            code = daily.get("weathercode", [])[i] if i < len(daily.get("weathercode", [])) else 0
            desc, icon = WMO_CODES.get(code, ("Невідомо", "🌡️"))
            result.append({
                "date": date,
                "temp_max": daily.get("temperature_2m_max", [])[i] if i < len(daily.get("temperature_2m_max", [])) else None,
                "temp_min": daily.get("temperature_2m_min", [])[i] if i < len(daily.get("temperature_2m_min", [])) else None,
                "precipitation": daily.get("precipitation_sum", [])[i] if i < len(daily.get("precipitation_sum", [])) else 0,
                "wind": daily.get("windspeed_10m_max", [])[i] if i < len(daily.get("windspeed_10m_max", [])) else None,
                "description": desc,
                "icon": icon,
                "type": "archive"
            })
        return result
    except Exception:
        return []

if __name__ == "__main__":
    app.run(debug=True, port=5000)
