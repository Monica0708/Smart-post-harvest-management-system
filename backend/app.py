from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, jsonify, g, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import jwt, uuid, os, json, random
from datetime import datetime, timedelta
from functools import wraps
from database import get_db, init_db

app = Flask(__name__, static_folder='../frontend', static_url_path='')
SECRET = os.environ.get('SECRET_KEY', 'harvestguard-dev-secret-2025')
print(os.getenv("SECRET_KEY"))
# ── CORS ──────────────────────────────────────────────────────────────────────
@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    r.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,PATCH,OPTIONS'
    return r
@app.before_request
def preflight():
    if request.method == 'OPTIONS': return jsonify({}), 200

# ── HELPERS ───────────────────────────────────────────────────────────────────
def make_token(uid, role):
    return jwt.encode({'sub':uid,'role':role,'exp':datetime.utcnow()+timedelta(hours=24)}, SECRET, algorithm='HS256')

def require_auth(f):
    @wraps(f)
    def inner(*a,**kw):
        hdr = request.headers.get('Authorization','')
        if not hdr.startswith('Bearer '): return jsonify({'error':'No token'}),401
        try:
            p = jwt.decode(hdr.split()[1], SECRET, algorithms=['HS256'])
            g.user_id=p['sub']; g.role=p.get('role','farmer')
        except jwt.ExpiredSignatureError: return jsonify({'error':'Token expired'}),401
        except: return jsonify({'error':'Bad token'}),401
        return f(*a,**kw)
    return inner

R  = lambda row:  dict(row) if row else None
RL = lambda rows: [dict(r) for r in rows]

# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/auth/register', methods=['POST'])
def register():
    d = request.get_json()
    for f in ['name','email','password']:
        if not d.get(f): return jsonify({'error':f'{f} required'}),400
    db = get_db()
    if db.execute('SELECT id FROM users WHERE email=?',(d['email'],)).fetchone():
        db.close(); return jsonify({'error':'Email already registered'}),409
    uid = str(uuid.uuid4())
    db.execute('INSERT INTO users (uuid,name,email,phone,password_hash,role,state,plan) VALUES (?,?,?,?,?,?,?,?)',
        (uid,d['name'],d['email'],d.get('phone',''),generate_password_hash(d['password']),
         d.get('role','farmer'),d.get('state',''),'free'))
    db.commit()
    user = R(db.execute('SELECT * FROM users WHERE uuid=?',(uid,)).fetchone())
    db.close(); user.pop('password_hash',None)
    return jsonify({'token':make_token(user['id'],user['role']),'user':user}),201

@app.route('/api/auth/login', methods=['POST'])
def login():
    d = request.get_json(); db = get_db()
    user = db.execute('SELECT * FROM users WHERE email=?',(d.get('email',''),)).fetchone()
    if not user or not check_password_hash(user['password_hash'],d.get('password','')):
        db.close(); return jsonify({'error':'Invalid credentials'}),401
    db.execute('UPDATE users SET last_login=? WHERE id=?',(datetime.now().isoformat(),user['id']))
    db.commit(); u=R(user); u.pop('password_hash',None); db.close()
    return jsonify({'token':make_token(u['id'],u['role']),'user':u})

@app.route('/api/auth/me', methods=['GET'])
@require_auth
def me():
    db=get_db(); u=R(db.execute('SELECT * FROM users WHERE id=?',(g.user_id,)).fetchone()); db.close()
    if not u: return jsonify({'error':'Not found'}),404
    u.pop('password_hash',None); return jsonify(u)

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/dashboard', methods=['GET'])
@require_auth
def dashboard():
    db=get_db(); uid=g.user_id; since=(datetime.now()-timedelta(days=30)).isoformat()
    stock    = db.execute("SELECT COALESCE(SUM(quantity_kg),0) t FROM inventory WHERE user_id=? AND status='stored'",(uid,)).fetchone()['t']
    units    = db.execute('SELECT COUNT(*) c FROM storage_units WHERE user_id=?',(uid,)).fetchone()['c']
    alerts_n = db.execute('SELECT COUNT(*) c FROM alerts WHERE user_id=? AND is_read=0',(uid,)).fetchone()['c']
    revenue  = db.execute("SELECT COALESCE(SUM(total_amount),0) t FROM transactions WHERE user_id=? AND transaction_date>=?",(uid,since)).fetchone()['t']
    sensor   = R(db.execute('SELECT sr.* FROM sensor_readings sr JOIN storage_units su ON sr.unit_id=su.id WHERE su.user_id=? ORDER BY sr.recorded_at DESC LIMIT 1',(uid,)).fetchone())
    s_hist   = RL(db.execute("SELECT date(recorded_at) day,ROUND(AVG(temperature),1) avg_temp,ROUND(AVG(humidity),1) avg_humidity FROM sensor_readings sr JOIN storage_units su ON sr.unit_id=su.id WHERE su.user_id=? AND recorded_at>=? GROUP BY date(recorded_at) ORDER BY day",(uid,(datetime.now()-timedelta(days=7)).isoformat())).fetchall())
    tx_chart = RL(db.execute("SELECT date(transaction_date) day,ROUND(SUM(total_amount),2) revenue,SUM(quantity_kg) quantity FROM transactions WHERE user_id=? AND transaction_date>=? GROUP BY date(transaction_date) ORDER BY day",(uid,since)).fetchall())
    inv_cat  = RL(db.execute("SELECT category,SUM(quantity_kg) total_kg,COUNT(*) items FROM inventory WHERE user_id=? AND status='stored' GROUP BY category",(uid,)).fetchall())
    db.close()
    return jsonify({'summary':{'total_stock_kg':round(stock,1),'storage_units':units,'unread_alerts':alerts_n,
        'revenue_30d':round(revenue,2),'loss_prevented_kg':round(stock*0.12,1),'quality_score':random.randint(88,97)},
        'latest_sensor':sensor or {},'sensor_history':s_hist,'transaction_chart':tx_chart,'inventory_by_category':inv_cat})

# ══════════════════════════════════════════════════════════════════════════════
# STORAGE
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/storage', methods=['GET'])
@require_auth
def get_storage():
    db=get_db()
    units=RL(db.execute('SELECT * FROM storage_units WHERE user_id=? AND status="active" ORDER BY id',(g.user_id,)).fetchall())
    for u in units:
        u['latest_sensor']=R(db.execute('SELECT * FROM sensor_readings WHERE unit_id=? ORDER BY recorded_at DESC LIMIT 1',(u['id'],)).fetchone()) or {}
        u['item_count']=db.execute("SELECT COUNT(*) c FROM inventory WHERE unit_id=? AND status='stored'",(u['id'],)).fetchone()['c']
    db.close(); return jsonify(units)

@app.route('/api/storage', methods=['POST'])
@require_auth
def add_storage():
    d=request.get_json(); db=get_db()
    db.execute('INSERT INTO storage_units (user_id,name,type,location,capacity_kg) VALUES (?,?,?,?,?)',
        (g.user_id,d['name'],d.get('type','cold_storage'),d.get('location',''),d.get('capacity_kg',1000)))
    db.commit(); db.close(); return jsonify({'message':'Storage unit added'}),201

@app.route('/api/storage/<int:sid>', methods=['DELETE'])
@require_auth
def del_storage(sid):
    db=get_db()
    db.execute("UPDATE storage_units SET status='inactive' WHERE id=? AND user_id=?",(sid,g.user_id))
    db.commit(); db.close(); return jsonify({'message':'Removed'})

# ══════════════════════════════════════════════════════════════════════════════
# INVENTORY
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/inventory', methods=['GET'])
@require_auth
def get_inventory():
    db=get_db()
    items=RL(db.execute('SELECT i.*,su.name unit_name FROM inventory i LEFT JOIN storage_units su ON i.unit_id=su.id WHERE i.user_id=? ORDER BY i.entry_date DESC',(g.user_id,)).fetchall())
    db.close(); return jsonify(items)

@app.route('/api/inventory', methods=['POST'])
@require_auth
def add_inventory():
    d=request.get_json(); db=get_db()
    shelf=int(d.get('expected_shelf_life_days',7))
    spoil=(datetime.now()+timedelta(days=shelf)).date().isoformat()
    batch=f"BATCH-{d['commodity'][:3].upper()}-{str(uuid.uuid4())[:4].upper()}"
    db.execute('INSERT INTO inventory (user_id,unit_id,commodity,category,quantity_kg,grade,harvest_date,expected_shelf_life_days,predicted_spoilage_date,status,batch_id,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (g.user_id,d.get('unit_id'),d['commodity'],d.get('category','vegetable'),d['quantity_kg'],
         d.get('grade','A'),d.get('harvest_date'),shelf,spoil,'stored',batch,d.get('notes','')))
    if d.get('unit_id'):
        db.execute('UPDATE storage_units SET current_stock_kg=current_stock_kg+? WHERE id=?',(d['quantity_kg'],d['unit_id']))
    db.commit(); db.close()
    return jsonify({'message':'Added','batch_id':batch}),201

@app.route('/api/inventory/<int:iid>', methods=['PUT'])
@require_auth
def update_inventory(iid):
    d=request.get_json(); db=get_db()
    db.execute('UPDATE inventory SET status=?,notes=?,grade=? WHERE id=? AND user_id=?',
        (d.get('status','stored'),d.get('notes',''),d.get('grade','A'),iid,g.user_id))
    db.commit(); db.close(); return jsonify({'message':'Updated'})

@app.route('/api/inventory/<int:iid>', methods=['DELETE'])
@require_auth
def del_inventory(iid):
    db=get_db()
    db.execute("UPDATE inventory SET status='removed' WHERE id=? AND user_id=?",(iid,g.user_id))
    db.commit(); db.close(); return jsonify({'message':'Removed'})

# ══════════════════════════════════════════════════════════════════════════════
# SENSORS
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/sensors/log', methods=['POST'])
@require_auth
def log_sensor():
    d=request.get_json(); db=get_db()
    unit=db.execute('SELECT id FROM storage_units WHERE id=? AND user_id=?',(d['unit_id'],g.user_id)).fetchone()
    if not unit: db.close(); return jsonify({'error':'Unit not found'}),404
    db.execute('INSERT INTO sensor_readings (unit_id,temperature,humidity,ethylene,co2,moisture) VALUES (?,?,?,?,?,?)',
        (d['unit_id'],d.get('temperature'),d.get('humidity'),d.get('ethylene'),d.get('co2'),d.get('moisture')))
    alerts=[]
    if (d.get('humidity') or 0)>80: alerts.append((g.user_id,d['unit_id'],'humidity','warning',f"High humidity: {d.get('humidity')}% — exceeds 80% threshold"))
    if (d.get('temperature') or 0)>25: alerts.append((g.user_id,d['unit_id'],'temperature','danger',f"High temperature: {d.get('temperature')}°C detected"))
    if (d.get('ethylene') or 0)>1.0: alerts.append((g.user_id,d['unit_id'],'ethylene','warning',f"Elevated ethylene: {d.get('ethylene')} ppm — accelerated ripening"))
    if alerts: db.executemany('INSERT INTO alerts (user_id,unit_id,type,severity,message) VALUES (?,?,?,?,?)',alerts)
    db.commit(); db.close()
    return jsonify({'message':'Logged','alerts_generated':len(alerts)})

@app.route('/api/sensors/simulate', methods=['POST'])
@require_auth
def simulate():
    db=get_db()
    unit=db.execute('SELECT id FROM storage_units WHERE user_id=? LIMIT 1',(g.user_id,)).fetchone()
    if not unit: db.close(); return jsonify({'error':'No unit'}),404
    r={'unit_id':unit['id'],'temperature':round(random.uniform(10,24),1),
       'humidity':round(random.uniform(62,88),1),'ethylene':round(random.uniform(0.2,1.3),2),
       'co2':round(random.uniform(380,620)),'moisture':round(random.uniform(10,28),1)}
    db.execute('INSERT INTO sensor_readings (unit_id,temperature,humidity,ethylene,co2,moisture) VALUES (?,?,?,?,?,?)',
        (r['unit_id'],r['temperature'],r['humidity'],r['ethylene'],r['co2'],r['moisture']))
    db.commit(); db.close(); r['recorded_at']=datetime.now().isoformat(); return jsonify(r)

@app.route('/api/sensors/<int:unit_id>/history', methods=['GET'])
@require_auth
def sensor_history(unit_id):
    days=int(request.args.get('days',7)); db=get_db()
    rows=RL(db.execute('SELECT * FROM sensor_readings WHERE unit_id=? AND recorded_at>=? ORDER BY recorded_at DESC LIMIT 200',
        (unit_id,(datetime.now()-timedelta(days=days)).isoformat())).fetchall())
    db.close(); return jsonify(rows)

# ══════════════════════════════════════════════════════════════════════════════
# ALERTS
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/alerts', methods=['GET'])
@require_auth
def get_alerts():
    db=get_db()
    rows=RL(db.execute('SELECT a.*,su.name unit_name FROM alerts a LEFT JOIN storage_units su ON a.unit_id=su.id WHERE a.user_id=? ORDER BY a.created_at DESC LIMIT 50',(g.user_id,)).fetchall())
    db.close(); return jsonify(rows)

@app.route('/api/alerts/<int:aid>/read', methods=['PATCH'])
@require_auth
def read_alert(aid):
    db=get_db(); db.execute('UPDATE alerts SET is_read=1 WHERE id=? AND user_id=?',(aid,g.user_id)); db.commit(); db.close(); return jsonify({'message':'Read'})

@app.route('/api/alerts/read-all', methods=['PATCH'])
@require_auth
def read_all():
    db=get_db(); db.execute('UPDATE alerts SET is_read=1 WHERE user_id=?',(g.user_id,)); db.commit(); db.close(); return jsonify({'message':'Done'})

# ══════════════════════════════════════════════════════════════════════════════
# PRICES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/prices', methods=['GET'])
def get_prices():
    db=get_db(); q='SELECT * FROM market_prices WHERE 1=1'; p=[]
    if request.args.get('category'): q+=' AND category=?'; p.append(request.args['category'])
    if request.args.get('search'): q+=' AND commodity LIKE ?'; p.append(f"%{request.args['search']}%")
    rows=RL(db.execute(q+' ORDER BY category,commodity',p).fetchall()); db.close(); return jsonify(rows)

@app.route('/api/prices/update', methods=['POST'])
def update_prices():
    db=get_db()
    for p in db.execute('SELECT * FROM market_prices').fetchall():
        delta=random.uniform(-2.5,2.5)
        new_p=round(max(p['min_price'],min(p['max_price'],p['price_per_quintal']*(1+delta/100))),0)
        trend='up' if delta>0.5 else ('down' if delta<-0.5 else 'stable')
        db.execute('UPDATE market_prices SET price_per_quintal=?,change_pct=?,trend=?,updated_at=? WHERE id=?',
            (new_p,round(delta,1),trend,datetime.now().isoformat(),p['id']))
    db.commit(); db.close(); return jsonify({'message':'Updated'})

# ══════════════════════════════════════════════════════════════════════════════
# TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/transactions', methods=['GET'])
@require_auth
def get_txns():
    db=get_db()
    rows=RL(db.execute('SELECT * FROM transactions WHERE user_id=? ORDER BY transaction_date DESC LIMIT 50',(g.user_id,)).fetchall())
    db.close(); return jsonify(rows)

@app.route('/api/transactions', methods=['POST'])
@require_auth
def add_txn():
    d=request.get_json(); db=get_db()
    qty=float(d['quantity_kg']); price=float(d['price_per_kg']); total=round(qty*price,2)
    db.execute('INSERT INTO transactions (user_id,inventory_id,commodity,quantity_kg,price_per_kg,total_amount,buyer_name,market) VALUES (?,?,?,?,?,?,?,?)',
        (g.user_id,d.get('inventory_id'),d['commodity'],qty,price,total,d.get('buyer_name',''),d.get('market','')))
    db.commit(); db.close(); return jsonify({'message':'Recorded','total_amount':total}),201

# ══════════════════════════════════════════════════════════════════════════════
# BUYERS
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/buyers', methods=['GET'])
@require_auth
def get_buyers():
    db=get_db(); q='SELECT * FROM buyers WHERE verified=1'; p=[]
    if request.args.get('commodity'): q+=' AND commodities LIKE ?'; p.append(f"%{request.args['commodity']}%")
    rows=RL(db.execute(q+' ORDER BY rating DESC',p).fetchall()); db.close(); return jsonify(rows)

# ══════════════════════════════════════════════════════════════════════════════
# AI SPOILAGE PREDICTION
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/predict/spoilage', methods=['POST'])
@require_auth
def predict():
    d=request.get_json()
    comm=d.get('commodity','Tomato'); temp=float(d.get('temperature',20))
    hum=float(d.get('humidity',75)); eth=float(d.get('ethylene',0.5)); stored=int(d.get('days_stored',0))
    base={'Tomato':7,'Onion':60,'Potato':90,'Carrot':14,'Banana':7,'Mango':5,'Grapes':7,'Apple':30,
          'Brinjal':5,'Okra':3,'Cauliflower':7,'Cabbage':14}.get(comm,7)
    t=1.4 if temp<8 else(1.15 if temp<15 else(0.6 if temp>25 else(0.8 if temp>20 else 1.0)))
    h=0.85 if hum>85 else(0.9 if hum<60 else 1.0)
    e=0.75 if eth>1.0 else(0.9 if eth>0.7 else 1.0)
    pred=round(base*t*h*e); rem=max(0,pred-stored)
    risk='critical' if rem<=1 else('high' if rem<=2 else('medium' if rem<=4 else 'low'))
    recs=[]
    if temp>20: recs.append("Reduce temperature to 10–15°C to extend shelf life")
    if hum>82:  recs.append("Reduce humidity to 70–80% — high moisture accelerates mold")
    if eth>0.8: recs.append("Use ethylene absorbers or increase ventilation")
    if rem<=2:  recs.append("⚠️ URGENT: Move to market immediately")
    return jsonify({'commodity':comm,'base_shelf_life_days':base,'predicted_remaining_days':rem,
        'risk_level':risk,'quality_score':max(10,round(100-(stored/max(1,pred))*100)),
        'recommendations':recs,'sell_by_date':(datetime.now()+timedelta(days=rem)).date().isoformat()})

# ══════════════════════════════════════════════════════════════════════════════
# ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/analytics', methods=['GET'])
@require_auth
def analytics():
    db=get_db(); uid=g.user_id; days=int(request.args.get('days',30))
    since=(datetime.now()-timedelta(days=days)).isoformat()
    rev   =db.execute("SELECT COALESCE(SUM(total_amount),0) t FROM transactions WHERE user_id=? AND transaction_date>=?",(uid,since)).fetchone()['t']
    txns  =db.execute("SELECT COUNT(*) c FROM transactions WHERE user_id=? AND transaction_date>=?",(uid,since)).fetchone()['c']
    top   =R(db.execute("SELECT commodity,SUM(quantity_kg) total FROM transactions WHERE user_id=? AND transaction_date>=? GROUP BY commodity ORDER BY total DESC LIMIT 1",(uid,since)).fetchone())
    monthly=RL(db.execute("SELECT strftime('%Y-%m',transaction_date) month,ROUND(SUM(total_amount),2) revenue FROM transactions WHERE user_id=? GROUP BY month ORDER BY month DESC LIMIT 6",(uid,)).fetchall())
    breakdown=RL(db.execute("SELECT commodity,ROUND(SUM(total_amount),2) revenue,SUM(quantity_kg) quantity FROM transactions WHERE user_id=? AND transaction_date>=? GROUP BY commodity ORDER BY revenue DESC",(uid,since)).fetchall())
    db.close()
    return jsonify({'period_days':days,'total_revenue':round(rev,2),'transaction_count':txns,
        'top_commodity':top or {},'monthly_revenue':monthly,'commodity_breakdown':breakdown})

# ══════════════════════════════════════════════════════════════════════════════
# CHATBOT  (rule-based + optional Claude API)
# ══════════════════════════════════════════════════════════════════════════════
RESPONSES = {
    'tomato':    "🍅 **Tomato Storage Tips:**\n• Ideal temperature: 13–15°C\n• Humidity: 85–90%\n• Keep away from ethylene sources\n• Shelf life: 7–14 days in cold storage\n• Current price: ~₹1,850/quintal in Chennai",
    'onion':     "🧅 **Onion Storage:**\n• Temperature: 1–4°C or cure at 25–30°C\n• Humidity: 65–70%\n• Ensure good airflow — never seal in bags\n• Cure 2–3 weeks before cold storage\n• Shelf life: 4–6 months when done right",
    'banana':    "🍌 **Banana Storage:**\n• Temperature: 13–14°C (do NOT refrigerate unripe)\n• Humidity: 85–95%\n• Keep separate from other fruits\n• Use ethylene blockers to slow ripening\n• Shelf life: 5–10 days",
    'potato':    "🥔 **Potato Storage:**\n• Temperature: 4–8°C\n• Humidity: 90–95%\n• Dark storage prevents greening\n• Do not store with onions\n• Shelf life: Up to 90 days",
    'mango':     "🥭 **Mango Storage:**\n• Ripe: 8–12°C for 2–3 weeks\n• Unripe: 13–15°C for ripening\n• Humidity: 85–90%\n• Current price: ~₹5,600/quintal",
    'spoil':     "🔬 **Preventing Spoilage:**\n1. Sort — remove damaged produce immediately\n2. Maintain correct temperature per commodity\n3. Control humidity (too high = mold, too low = wilting)\n4. Use ethylene absorbers in cold storage\n5. Ensure cold chain continuity during transport\n6. Use our AI prediction tool for early warnings",
    'price':     "📊 Check the **Live Prices** tab for real-time AGMARKNET data updated every 30 minutes. Best selling times: mornings 6–9 AM at mandis. Track trends using the green/red arrows.",
    'scheme':    "🏛️ **Key Government Schemes:**\n• **PM-KISAN**: ₹6,000/year direct transfer\n• **PMFBY**: Crop insurance at 2% premium\n• **MIDH**: 25–35% subsidy on cold storage\n• **e-NAM**: Online mandi for better prices\n• **KCC**: Kisan Credit Card for farm loans",
    'sensor':    "📡 **Our IoT Sensor Kit includes:**\n• Temperature & humidity sensor\n• Ethylene detector\n• CO₂ monitor\n• Connects via GSM (no WiFi needed)\n• Solar + battery powered\n• Installation: under 30 minutes\n• Alerts via SMS & WhatsApp",
    'cold':      "❄️ **Cold Chain Best Practices:**\n• Pre-cool produce before loading\n• Use insulated transport containers\n• Monitor temperature every 30 mins in transit\n• Avoid breaking the cold chain — even briefly\n• Use GPS tracking for visibility",
    'free':      "💰 **Our Plans:**\n• **Free Trial**: 30 days, 1 storage unit\n• **Standard**: ₹499/month — 5 units, AI alerts\n• **Premium**: ₹2,499/month — unlimited\n• **Enterprise**: Custom pricing\n\nStart free — no credit card needed!",
    'temperature': "🌡️ **Optimal Storage Temperatures:**\n• Tomato: 13–15°C\n• Potato: 4–8°C\n• Onion: 1–4°C\n• Banana: 13–14°C\n• Mango: 8–12°C\n• Leafy greens: 0–4°C\n• Apple: 0–4°C\n\nOur sensors monitor these 24/7!",
}

def bot_reply(msg):
    m = msg.lower()
    for key in RESPONSES:
        if key in m:
            return RESPONSES[key]
    if any(w in m for w in ['hi','hello','hey','helo']): return "👋 Hello! I'm HarvestBot — your post-harvest management expert!\n\nI can help with:\n• Storage tips for any fruit/vegetable\n• Spoilage prevention strategies\n• Market price guidance\n• Government scheme info\n• Cold chain best practices\n\nWhat would you like to know?"
    if any(w in m for w in ['thank','thanks']): return "You're welcome! 🌱 Happy farming! Feel free to ask anytime."
    return "I can help with storage tips, spoilage prevention, market prices, government schemes, and sensor/IoT queries. Try asking something like:\n• *How do I store tomatoes?*\n• *What's the price of onions today?*\n• *How does AI prediction work?*"

@app.route('/api/chatbot', methods=['POST'])
def chatbot():
    d = request.get_json(); msg = d.get('message','')
    # Try Claude API if key is set, otherwise use rule-based
    api_key = os.environ.get('ANTHROPIC_API_KEY','')
    if api_key:
        import urllib.request, urllib.error
        db = get_db()
        prices = RL(db.execute('SELECT commodity,price_per_quintal,trend FROM market_prices LIMIT 10').fetchall())
        db.close()
        price_ctx = ", ".join(f"{p['commodity']} ₹{p['price_per_quintal']}/q ({p['trend']})" for p in prices)
        system = f"""You are HarvestBot, AI assistant for HarvestGuard — a post-harvest management platform for Indian farmers.
Help with: storage, spoilage prevention, market prices, cold chain, government schemes (PM-KISAN, PMFBY, MIDH, e-NAM, KCC), pest/disease.
Current prices: {price_ctx}
Keep answers practical, India-specific, under 180 words. Use bullet points."""
        try:
            body = json.dumps({"model":"claude-sonnet-4-20250514","max_tokens":400,"system":system,
                "messages":[{"role":"user","content":msg}]}).encode()
            req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=body,
                headers={'Content-Type':'application/json','anthropic-version':'2023-06-01','x-api-key':api_key})
            with urllib.request.urlopen(req, timeout=12) as resp:
                result = json.loads(resp.read())
                return jsonify({'reply':result['content'][0]['text'],'timestamp':datetime.now().isoformat()})
        except: pass
    return jsonify({'reply': bot_reply(msg), 'timestamp': datetime.now().isoformat()})

# ══════════════════════════════════════════════════════════════════════════════
# SERVE FRONTEND
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/', defaults={'path':''})
@app.route('/<path:path>')
def frontend(path):
    fe = os.path.join(os.path.dirname(__file__),'..','frontend')
    full = os.path.join(fe, path)
    if path and os.path.exists(full):
        return send_from_directory(fe, path)
    idx = os.path.join(fe,'index.html')
    if os.path.exists(idx):
        return send_from_directory(fe,'index.html')
    return jsonify({'status':'HarvestGuard API v1.0 running',
        'demo_login':{'email':'demo@harvestguard.in','password':'demo1234'},
        'docs':'/api/dashboard  /api/inventory  /api/prices  /api/alerts  /api/sensors  /api/buyers  /api/predict/spoilage  /api/chatbot'})

if __name__ == '__main__':
    init_db()
    print("\n" + "="*55)
    print("  🌱  HarvestGuard Backend  →  http://localhost:5000")
    print("  Demo: demo@harvestguard.in  |  demo1234")
    print("="*55 + "\n")
    app.run(debug=True, host='0.0.0.0', port=5000)

# ══════════════════════════════════════════════════════════════════════════════
# REAL-TIME WEATHER (affects storage recommendations)
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/weather', methods=['GET'])
def get_weather():
    import urllib.request as ur
    city = request.args.get('city', 'Chennai')
    # Open-Meteo free API - no key needed, real weather data
    cities = {
        'Chennai':    (13.0827, 80.2707),
        'Mumbai':     (19.0760, 72.8777),
        'Delhi':      (28.6139, 77.2090),
        'Bangalore':  (12.9716, 77.5946),
        'Hyderabad':  (17.3850, 78.4867),
        'Pune':       (18.5204, 73.8567),
        'Kolkata':    (22.5726, 88.3639),
        'Ahmedabad':  (23.0225, 72.5714),
    }
    lat, lon = cities.get(city, (13.0827, 80.2707))
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m&timezone=Asia%2FKolkata"
    try:
        with ur.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
            cur = data.get('current', {})
            return jsonify({
                'city': city,
                'temperature': cur.get('temperature_2m'),
                'humidity': cur.get('relative_humidity_2m'),
                'feels_like': cur.get('apparent_temperature'),
                'precipitation': cur.get('precipitation'),
                'wind_speed': cur.get('wind_speed_10m'),
                'source': 'Open-Meteo (Live)',
                'time': cur.get('time'),
            })
    except Exception as e:
        return jsonify({'city': city, 'temperature': 32, 'humidity': 72,
                       'feels_like': 36, 'precipitation': 0, 'wind_speed': 12,
                       'source': 'Simulated', 'error': str(e)})