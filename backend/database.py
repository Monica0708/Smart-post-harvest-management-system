"""
HarvestGuard – Database Layer
Handles all SQLite operations, schema creation, and seed data.
"""
import sqlite3, os, random, uuid as _uuid
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'harvestguard.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db(); c = conn.cursor()

    # ── Users ────────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid          TEXT UNIQUE NOT NULL,
        name          TEXT NOT NULL,
        email         TEXT UNIQUE NOT NULL,
        phone         TEXT,
        password_hash TEXT NOT NULL,
        role          TEXT DEFAULT 'farmer',
        state         TEXT DEFAULT '',
        city          TEXT DEFAULT '',
        plan          TEXT DEFAULT 'free',
        sms_alerts    INTEGER DEFAULT 1,
        sms_time      TEXT DEFAULT '07:00',
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        last_login    TEXT
    )''')

    # ── Price Cache (pulled from data.gov.in / eNAM) ─────────
    c.execute('''CREATE TABLE IF NOT EXISTS market_prices (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        commodity        TEXT NOT NULL,
        variety          TEXT DEFAULT '',
        category         TEXT NOT NULL,
        emoji            TEXT DEFAULT '🌾',
        state            TEXT NOT NULL,
        district         TEXT NOT NULL,
        market           TEXT NOT NULL,
        min_price        REAL,
        max_price        REAL,
        modal_price      REAL NOT NULL,
        change_pct       REAL DEFAULT 0,
        trend            TEXT DEFAULT 'stable',
        arrival_date     TEXT,
        source           TEXT DEFAULT 'data.gov.in',
        fetched_at       TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── State/City/Commodity index for fast lookup ────────────
    c.execute('CREATE INDEX IF NOT EXISTS idx_prices_state   ON market_prices(state)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_prices_district ON market_prices(district)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_prices_commodity ON market_prices(commodity)')

    # ── Storage Units ────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS storage_units (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id          INTEGER NOT NULL,
        name             TEXT NOT NULL,
        type             TEXT DEFAULT 'cold_storage',
        location         TEXT,
        state            TEXT,
        city             TEXT,
        capacity_kg      REAL DEFAULT 1000,
        current_stock_kg REAL DEFAULT 0,
        status           TEXT DEFAULT 'active',
        created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # ── Sensor Readings ──────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS sensor_readings (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        unit_id     INTEGER NOT NULL,
        temperature REAL,
        humidity    REAL,
        ethylene    REAL,
        co2         REAL,
        moisture    REAL,
        recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(unit_id) REFERENCES storage_units(id)
    )''')

    # ── Inventory ────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id                       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id                  INTEGER NOT NULL,
        unit_id                  INTEGER,
        commodity                TEXT NOT NULL,
        category                 TEXT NOT NULL,
        quantity_kg              REAL NOT NULL,
        grade                    TEXT DEFAULT 'A',
        harvest_date             TEXT,
        entry_date               TEXT DEFAULT CURRENT_TIMESTAMP,
        expected_shelf_life_days INTEGER DEFAULT 7,
        predicted_spoilage_date  TEXT,
        status                   TEXT DEFAULT 'stored',
        batch_id                 TEXT,
        notes                    TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(unit_id) REFERENCES storage_units(id)
    )''')

    # ── Alerts ───────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS alerts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        unit_id    INTEGER,
        type       TEXT NOT NULL,
        severity   TEXT DEFAULT 'warning',
        message    TEXT NOT NULL,
        is_read    INTEGER DEFAULT 0,
        sms_sent   INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # ── Transactions ─────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id          INTEGER NOT NULL,
        inventory_id     INTEGER,
        commodity        TEXT NOT NULL,
        quantity_kg      REAL NOT NULL,
        price_per_kg     REAL NOT NULL,
        total_amount     REAL NOT NULL,
        buyer_name       TEXT,
        market           TEXT,
        state            TEXT,
        city             TEXT,
        transaction_date TEXT DEFAULT CURRENT_TIMESTAMP,
        status           TEXT DEFAULT 'completed',
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # ── Buyers ───────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS buyers (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        company     TEXT,
        type        TEXT DEFAULT 'trader',
        commodities TEXT,
        state       TEXT,
        city        TEXT,
        phone       TEXT,
        rating      REAL DEFAULT 4.0,
        verified    INTEGER DEFAULT 1,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── SMS Log ──────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS sms_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        phone      TEXT NOT NULL,
        message    TEXT NOT NULL,
        type       TEXT DEFAULT 'alert',
        status     TEXT DEFAULT 'sent',
        sent_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # ── Price Fetch Log ──────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS price_fetch_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        source     TEXT NOT NULL,
        records    INTEGER DEFAULT 0,
        status     TEXT DEFAULT 'success',
        error      TEXT,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    conn.commit()
    _seed(conn)
    conn.close()
    print("[DB] Database initialized successfully.")

# ─────────────────────────────────────────────────────────────
#  SEED DATA  (realistic Indian mandi prices per state/city)
# ─────────────────────────────────────────────────────────────
SEED_PRICES = [
    # (commodity, variety, category, emoji, state, district, market, min, max, modal, change, trend)
    # Tamil Nadu
    ('Tomato','Local','vegetable','🍅','Tamil Nadu','Chennai','Koyambedu',1400,2100,1850,4.2,'up'),
    ('Tomato','Hybrid','vegetable','🍅','Tamil Nadu','Coimbatore','Ukkadam',1300,2000,1700,2.1,'up'),
    ('Onion','Big','vegetable','🧅','Tamil Nadu','Chennai','Koyambedu',1800,2500,2100,-3.5,'down'),
    ('Potato','Jyoti','vegetable','🥔','Tamil Nadu','Chennai','Koyambedu',1200,1800,1450,0.5,'stable'),
    ('Banana','Nendran','fruit','🍌','Tamil Nadu','Krishnagiri','Krishnagiri',2800,3800,3200,5.8,'up'),
    ('Banana','Robusta','fruit','🍌','Tamil Nadu','Trichy','Trichy Market',2200,3000,2600,2.3,'up'),
    ('Mango','Alphonso','fruit','🥭','Tamil Nadu','Salem','Salem',4500,7000,5800,12.3,'up'),
    ('Brinjal','Round','vegetable','🍆','Tamil Nadu','Coimbatore','Ukkadam',800,1400,1100,6.5,'up'),
    ('Green Chilli','Local','vegetable','🌶️','Tamil Nadu','Guntur','Guntur',3500,5200,4200,18.2,'up'),
    ('Carrot','Local','vegetable','🥕','Tamil Nadu','Ooty','Ooty',2400,3500,2900,-2.1,'down'),

    # Maharashtra
    ('Tomato','Desi','vegetable','🍅','Maharashtra','Nashik','Lasalgaon',1200,2000,1600,-5.3,'down'),
    ('Onion','Nasik Red','vegetable','🧅','Maharashtra','Nashik','Lasalgaon',1700,2800,2200,-8.1,'down'),
    ('Onion','White','vegetable','🧅','Maharashtra','Pune','Pune Market',1900,2700,2300,-3.2,'down'),
    ('Grapes','Thompson','fruit','🍇','Maharashtra','Sangli','Sangli',6500,9500,7800,-4.5,'down'),
    ('Pomegranate','Bhagwa','fruit','🍑','Maharashtra','Solapur','Solapur',6800,10500,8200,9.2,'up'),
    ('Potato','Chipsona','vegetable','🥔','Maharashtra','Pune','Pune Market',1100,1700,1400,1.2,'stable'),
    ('Cauliflower','Local','vegetable','🥦','Maharashtra','Pune','Pune Market',900,1700,1300,-5.6,'down'),

    # Karnataka
    ('Tomato','Bangalore','vegetable','🍅','Karnataka','Bangalore','APMC',1300,2200,1750,7.8,'up'),
    ('Brinjal','Dharwad','vegetable','🍆','Karnataka','Dharwad','Dharwad',700,1300,1000,4.1,'up'),
    ('Mango','Badami','fruit','🥭','Karnataka','Ramanagara','Channapatna',3800,6200,5000,8.4,'up'),
    ('Onion','Red','vegetable','🧅','Karnataka','Bellary','Bellary',1600,2400,2000,-2.8,'down'),

    # Andhra Pradesh
    ('Green Chilli','Jwala','vegetable','🌶️','Andhra Pradesh','Guntur','Guntur',3800,5500,4500,22.1,'up'),
    ('Tomato','Local','vegetable','🍅','Andhra Pradesh','Chittoor','Madanapalle',1100,1900,1500,-1.2,'stable'),
    ('Papaya','Red Lady','fruit','🍈','Andhra Pradesh','Krishna','Vijayawada',1400,2300,1800,3.5,'up'),

    # Uttar Pradesh
    ('Potato','Kufri Jyoti','vegetable','🥔','Uttar Pradesh','Agra','Agra',1100,1650,1350,1.8,'stable'),
    ('Onion','Local','vegetable','🧅','Uttar Pradesh','Aligarh','Aligarh',1500,2200,1850,-4.1,'down'),
    ('Mango','Dasheri','fruit','🥭','Uttar Pradesh','Lucknow','Lucknow',3200,5500,4200,14.6,'up'),
    ('Wheat','Lokwan','grain','🌾','Uttar Pradesh','Kanpur','Kanpur',2100,2500,2275,0.0,'stable'),

    # Punjab
    ('Wheat','PBW-343','grain','🌾','Punjab','Ludhiana','Ludhiana',2150,2450,2290,0.5,'stable'),
    ('Potato','Kufri','vegetable','🥔','Punjab','Jalandhar','Jalandhar',1000,1550,1280,2.3,'stable'),

    # Gujarat
    ('Mango','Kesar','fruit','🥭','Gujarat','Junagadh','Junagadh',4800,7200,5900,11.2,'up'),
    ('Pomegranate','Bhagwa','fruit','🍑','Gujarat','Kutch','Bhuj',7200,10000,8500,6.5,'up'),
    ('Groundnut','Bold','oilseed','🥜','Gujarat','Rajkot','Rajkot',5200,6400,5800,2.1,'up'),
    ('Cotton','Medium','cash_crop','🌿','Gujarat','Surendranagar','Surendranagar',6100,7200,6600,1.5,'stable'),

    # West Bengal
    ('Rice','Sona Masoori','grain','🌾','West Bengal','Bardhaman','Bardhaman',2000,2500,2200,0.0,'stable'),
    ('Potato','Chandramukhi','vegetable','🥔','West Bengal','Hooghly','Hooghly',950,1450,1200,3.2,'up'),
    ('Tomato','Local','vegetable','🍅','West Bengal','Kolkata','Koley Market',1500,2300,1900,6.1,'up'),

    # Himachal Pradesh
    ('Apple','Shimla','fruit','🍎','Himachal Pradesh','Shimla','Shimla',8000,12000,9800,5.4,'up'),
    ('Apple','Royal Delicious','fruit','🍎','Himachal Pradesh','Kullu','Kullu',7500,11000,9200,3.2,'up'),
    ('Pea','Local','vegetable','🫛','Himachal Pradesh','Shimla','Shimla',3500,5200,4200,8.1,'up'),

    # Rajasthan
    ('Cumin','Local','spice','🌿','Rajasthan','Jodhpur','Jodhpur',14000,18000,16000,5.2,'up'),
    ('Onion','Red','vegetable','🧅','Rajasthan','Alwar','Alwar',1400,2100,1750,-6.3,'down'),

    # Kerala
    ('Coconut','Local','fruit','🥥','Kerala','Thrissur','Thrissur',2000,2800,2400,1.2,'stable'),
    ('Banana','Poovan','fruit','🍌','Kerala','Palakkad','Palakkad',2500,3500,3000,3.8,'up'),
    ('Rubber','RSS4','cash_crop','🌿','Kerala','Kottayam','Kottayam',15000,18000,16500,2.1,'up'),
]

SEED_BUYERS = [
    ('Raj Traders','Raj Enterprises Pvt Ltd','exporter','Tomato,Onion,Potato','Tamil Nadu','Chennai','+91 98100 11111',4.8),
    ('Fresh Farm Co.','Fresh Farm India','processor','Mango,Banana,Grapes','Maharashtra','Pune','+91 98200 22222',4.6),
    ('KrishiMart','KrishiMart India Ltd','retailer','Tomato,Carrot,Cabbage','Karnataka','Bangalore','+91 98300 33333',4.4),
    ('AgriExport Hub','AgriExport Hub Ltd','exporter','Mango,Pomegranate','Gujarat','Ahmedabad','+91 98400 44444',4.9),
    ('Metro Vegetables','Metro Cash & Carry','retailer','All Vegetables','Delhi','New Delhi','+91 98500 55555',4.7),
    ('AgroStar','AgroStar India','platform','All Commodities','Maharashtra','Mumbai','+91 98600 66666',4.5),
    ('Ninjacart','Ninjacart Pvt Ltd','aggregator','All Vegetables,Fruits','Karnataka','Bangalore','+91 98700 77777',4.8),
    ('BigBasket','Supermarket Grocery','retailer','All Vegetables,Fruits','Karnataka','Bangalore','+91 98800 88888',4.6),
    ('ITC Agri','ITC Limited Agribusiness','processor','Wheat,Rice,Potato','Uttar Pradesh','Lucknow','+91 98900 99999',4.7),
    ('APEDA Export','APEDA Certified Exporter','exporter','Mango,Grapes,Pomegranate','Maharashtra','Nashik','+91 99000 00000',4.9),
]

def _seed(conn):
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM market_prices")
    if c.fetchone()[0] > 0:
        return

    today = datetime.now().date().isoformat()
    c.executemany('''INSERT INTO market_prices
        (commodity,variety,category,emoji,state,district,market,
         min_price,max_price,modal_price,change_pct,trend,arrival_date,source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        [(r[0],r[1],r[2],r[3],r[4],r[5],r[6],r[7],r[8],r[9],r[10],r[11],today,'seed/agmarknet')
         for r in SEED_PRICES])

    # Demo user
    uid = str(_uuid.uuid4())
    c.execute('''INSERT OR IGNORE INTO users
        (uuid,name,email,phone,password_hash,role,state,city,plan,sms_alerts)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (uid,'Demo Farmer','demo@harvestguard.in','+919876543210',
         generate_password_hash('demo1234'),'farmer','Tamil Nadu','Chennai','standard',1))
    user_id = c.lastrowid or 1

    c.execute('''INSERT OR IGNORE INTO storage_units
        (user_id,name,type,location,state,city,capacity_kg,current_stock_kg)
        VALUES (?,?,?,?,?,?,?,?)''',
        (user_id,'Main Cold Storage','cold_storage','Koyambedu, Chennai','Tamil Nadu','Chennai',2000,1450))
    c.execute('''INSERT OR IGNORE INTO storage_units
        (user_id,name,type,location,state,city,capacity_kg,current_stock_kg)
        VALUES (?,?,?,?,?,?,?,?)''',
        (user_id,'Onion Shed','ambient','Koyambedu','Tamil Nadu','Chennai',1000,600))

    for comm,cat,qty,grade,days_ago,shelf in [
        ('Tomato','vegetable',450,'A',-3,7),
        ('Onion','vegetable',320,'B',-10,30),
        ('Banana','fruit',180,'A',-1,5),
        ('Potato','vegetable',500,'A',-15,45)]:
        harvest = (datetime.now()+timedelta(days=days_ago)).date().isoformat()
        spoil   = (datetime.now()+timedelta(days=shelf)).date().isoformat()
        c.execute('''INSERT OR IGNORE INTO inventory
            (user_id,unit_id,commodity,category,quantity_kg,grade,
             harvest_date,expected_shelf_life_days,predicted_spoilage_date,status,batch_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (user_id,1,comm,cat,qty,grade,harvest,shelf,spoil,'stored',
             f'BATCH-{comm[:3].upper()}-001'))

    base = datetime.now()
    for day in range(7):
        for hour in range(0,24,4):
            ts = (base-timedelta(days=day,hours=hour)).isoformat()
            c.execute('''INSERT INTO sensor_readings
                (unit_id,temperature,humidity,ethylene,co2,moisture,recorded_at)
                VALUES (?,?,?,?,?,?,?)''',
                (1,round(random.uniform(10,22),1),round(random.uniform(65,85),1),
                 round(random.uniform(0.3,1.2),2),round(random.uniform(400,600)),
                 round(random.uniform(10,25),1),ts))

    c.executemany('INSERT OR IGNORE INTO alerts (user_id,unit_id,type,severity,message) VALUES (?,?,?,?,?)',[
        (user_id,1,'humidity','warning','Humidity in Main Cold Storage reached 84% — exceeds 80% threshold'),
        (user_id,1,'spoilage','danger','Banana batch BATCH-BAN-001 predicted to spoil in 2 days — act now'),
        (user_id,None,'price','info','Tomato prices up 12% in Koyambedu today — good time to sell'),
        (user_id,1,'temperature','warning','Temperature spike to 22°C detected at 3 AM'),
    ])

    c.executemany('''INSERT OR IGNORE INTO buyers
        (name,company,type,commodities,state,city,phone,rating,verified)
        VALUES (?,?,?,?,?,?,?,?,1)''', SEED_BUYERS)

    for _ in range(12):
        days_ago = random.randint(1,30); qty=random.randint(50,300)
        price=round(random.uniform(15,60),2); comm=random.choice(['Tomato','Onion','Banana','Potato'])
        c.execute('''INSERT INTO transactions
            (user_id,commodity,quantity_kg,price_per_kg,total_amount,buyer_name,market,state,city,transaction_date)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (user_id,comm,qty,price,round(qty*price,2),
             random.choice(['Raj Traders','KrishiMart','Metro Vegetables']),
             random.choice(['Koyambedu','Lasalgaon','Bangalore']),
             'Tamil Nadu','Chennai',
             (datetime.now()-timedelta(days=days_ago)).isoformat()))

    conn.commit()
    print(f"[DB] Seeded {len(SEED_PRICES)} price records across states.")
    print("[DB] Demo: demo@harvestguard.in / demo1234")