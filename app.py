#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Uther Web Order — Mobile-first order page + Telegram notify + tracking
Deploy on Railway.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, render_template_string
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uther-web")

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "5922115358").strip()
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
ORDERS_FILE = DATA_DIR / "web_orders.json"

# 付款地址（按仓库）
PAY_ADDRESSES = {
    "us": {
        "BTC": "bc1qh3jn87gse6xjw5jqdj6wz503apxrasxkq9uzlt",
        "ERC20": "0xBc37093DAC79a008D91e32E808a8bc1A2a5126E4",
        "SOL": "HRSZRKe2SfQNfnD3BRm6JFsktd3ZwLtYz6Z47GPovS6f",
    },
    "eu": {
        "BTC": "bc1qcvxk38mfwhusypmseqpzpczzz4n8p0k0xf360h",
        "ERC20": "0x554830Fc4E68bEa1a5fbB85c8E729Af795089D6A",
        "SOL": "8LVWxCqRGgGM1vn19bjTTFVckW9M6SC8hHriwr8aoeWg",
    },
    "au": {
        "BTC": "bc1qcvxk38mfwhusypmseqpzpczzz4n8p0k0xf360h",
        "ERC20": "0x554830Fc4E68bEa1a5fbB85c8E729Af795089D6A",
        "SOL": "8LVWxCqRGgGM1vn19bjTTFVckW9M6SC8hHriwr8aoeWg",
    },
    "uk": {
        "BTC": "bc1qcvxk38mfwhusypmseqpzpczzz4n8p0k0xf360h",
        "ERC20": "0x554830Fc4E68bEa1a5fbB85c8E729Af795089D6A",
        "SOL": "8LVWxCqRGgGM1vn19bjTTFVckW9M6SC8hHriwr8aoeWg",
    },
}

# 默认商品（可从 /data/products.json 覆盖）
DEFAULT_PRODUCTS = {
    "reta": {"name": "Reta 30mg (10 vials)", "price": 185, "warehouse": "us"},
    "tirz": {"name": "Tirz 30mg (10 vials)", "price": 130, "warehouse": "us"},
    "epitalon": {"name": "Epitalon 50mg (10 vials)", "price": 140, "warehouse": "us"},
    "ss31": {"name": "SS-31 28mg (10 vials)", "price": 140, "warehouse": "us"},
    "tb4": {"name": "TB4 20mg (10 vials)", "price": 200, "warehouse": "us"},
}

WAREHOUSES = {
    "us": "🇺🇸 USA",
    "eu": "🇪🇺 Europe",
    "au": "🇦🇺 Australia",
    "uk": "🇬🇧 UK",
}

def load_products():
    pfile = DATA_DIR / "products.json"
    if pfile.exists():
        try:
            data = json.loads(pfile.read_text(encoding="utf-8"))
            # 兼容原机器人格式
            out = {}
            for k, v in data.items():
                price = list(v.get("price_tiers", {1: 0}).values())[0] if v.get("price_tiers") else v.get("price", 0)
                out[k] = {
                    "name": v.get("name", k),
                    "price": float(price),
                    "warehouse": v.get("warehouse", "us"),
                }
            return out
        except Exception as e:
            logger.error(f"load products: {e}")
    return DEFAULT_PRODUCTS

def load_orders():
    if ORDERS_FILE.exists():
        try:
            return json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_orders(orders):
    ORDERS_FILE.write_text(json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8")

def send_tg(chat_id, text):
    if not BOT_TOKEN:
        logger.warning("No BOT_TOKEN")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        logger.error(f"TG send: {e}")
        return False

# ==================== HTML ====================
PAGE_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Uther Order</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f5f7; color: #1d1d1f; }
.container { max-width: 480px; margin: 0 auto; padding: 16px; }
h1 { font-size: 22px; margin-bottom: 16px; text-align: center; }
.section { background: #fff; border-radius: 12px; padding: 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.section h2 { font-size: 15px; margin-bottom: 10px; color: #333; }
select, input, textarea { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 16px; margin-bottom: 8px; }
textarea { min-height: 80px; resize: vertical; }
.product { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #eee; }
.product:last-child { border-bottom: none; }
.product .info { flex: 1; }
.product .name { font-weight: 500; }
.product .price { color: #0071e3; font-size: 14px; }
.qty { display: flex; align-items: center; gap: 8px; }
.qty button { width: 32px; height: 32px; border: 1px solid #ddd; background: #f5f5f7; border-radius: 6px; font-size: 18px; cursor: pointer; }
.qty span { min-width: 24px; text-align: center; }
.total { font-size: 18px; font-weight: 600; text-align: right; margin-top: 8px; }
.btn { width: 100%; padding: 14px; background: #0071e3; color: #fff; border: none; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; }
.btn:disabled { background: #ccc; }
.pay-box { background: #f0f7ff; border-radius: 8px; padding: 12px; margin-top: 8px; font-size: 13px; word-break: break-all; }
.pay-box code { display: block; margin: 4px 0; font-family: monospace; }
.success { text-align: center; padding: 40px 16px; }
.success h2 { color: #34c759; margin-bottom: 8px; }
.hidden { display: none; }
.admin-link { text-align: center; margin-top: 20px; font-size: 12px; color: #999; }
.admin-link a { color: #0071e3; }
</style>
</head>
<body>
<div class="container">
  <h1>Uther Order</h1>

  <div id="form-view">
    <div class="section">
      <h2>Warehouse</h2>
      <select id="warehouse" onchange="filterProducts()">
        <option value="us">🇺🇸 USA</option>
        <option value="eu">🇪🇺 Europe</option>
        <option value="au">🇦🇺 Australia</option>
        <option value="uk">🇬🇧 UK</option>
      </select>
    </div>

    <div class="section">
      <h2>Products</h2>
      <div id="products"></div>
      <div class="total">Total: $<span id="total">0</span></div>
    </div>

    <div class="section">
      <h2>Shipping Address</h2>
      <textarea id="address" placeholder="Full name, street, city, state, ZIP, phone"></textarea>
    </div>

    <div class="section">
      <h2>Payment Network</h2>
      <select id="network">
        <option value="BTC">BTC</option>
        <option value="ERC20">USDT/USDC (ERC20)</option>
        <option value="SOL">USDC (Solana)</option>
      </select>
      <div class="pay-box" id="pay-info"></div>
    </div>

    <div class="section">
      <h2>Your Telegram (optional, for tracking)</h2>
      <input id="tg_user" placeholder="@username or leave blank">
    </div>

    <button class="btn" id="submit-btn" onclick="submitOrder()">Place Order</button>
  </div>

  <div id="success-view" class="hidden success">
    <h2>Order Received</h2>
    <p>Order ID: <strong id="order-id"></strong></p>
    <p style="margin-top:12px;">Please send payment to the address shown, then wait for confirmation.</p>
    <button class="btn" style="margin-top:20px;" onclick="location.reload()">New Order</button>
  </div>

  <div class="admin-link"><a href="/admin">Admin</a></div>
</div>

<script>
const PRODUCTS = {{ products | tojson }};
const PAY = {{ pay | tojson }};
let cart = {};

function filterProducts() {
  const wh = document.getElementById('warehouse').value;
  const box = document.getElementById('products');
  box.innerHTML = '';
  cart = {};
  for (const [k, p] of Object.entries(PRODUCTS)) {
    if (p.warehouse && p.warehouse !== wh && wh !== 'us') continue;
    const div = document.createElement('div');
    div.className = 'product';
    div.innerHTML = `
      <div class="info">
        <div class="name">${p.name}</div>
        <div class="price">$${p.price}</div>
      </div>
      <div class="qty">
        <button onclick="changeQty('${k}', -1)">−</button>
        <span id="qty-${k}">0</span>
        <button onclick="changeQty('${k}', 1)">+</button>
      </div>`;
    box.appendChild(div);
  }
  updateTotal();
  updatePay();
}

function changeQty(key, d) {
  cart[key] = Math.max(0, (cart[key] || 0) + d);
  document.getElementById('qty-' + key).textContent = cart[key];
  updateTotal();
}

function updateTotal() {
  let t = 0;
  for (const [k, q] of Object.entries(cart)) {
    if (q > 0 && PRODUCTS[k]) t += PRODUCTS[k].price * q;
  }
  document.getElementById('total').textContent = t.toFixed(2);
  return t;
}

function updatePay() {
  const wh = document.getElementById('warehouse').value;
  const net = document.getElementById('network').value;
  const addr = (PAY[wh] || PAY.us)[net] || '';
  document.getElementById('pay-info').innerHTML = addr ? `<b>${net}</b><code>${addr}</code>` : 'No address';
}

document.getElementById('warehouse').onchange = filterProducts;
document.getElementById('network').onchange = updatePay;

function submitOrder() {
  const items = [];
  for (const [k, q] of Object.entries(cart)) {
    if (q > 0) items.push({ key: k, name: PRODUCTS[k].name, qty: q, price: PRODUCTS[k].price });
  }
  if (!items.length) { alert('Please select at least one product'); return; }
  const address = document.getElementById('address').value.trim();
  if (address.length < 10) { alert('Please enter a full address'); return; }

  const btn = document.getElementById('submit-btn');
  btn.disabled = true;
  btn.textContent = 'Submitting...';

  fetch('/api/order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      warehouse: document.getElementById('warehouse').value,
      network: document.getElementById('network').value,
      items: items,
      total: updateTotal(),
      address: address,
      tg_user: document.getElementById('tg_user').value.trim(),
    }),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) {
      document.getElementById('form-view').classList.add('hidden');
      document.getElementById('success-view').classList.remove('hidden');
      document.getElementById('order-id').textContent = d.order_id;
    } else {
      alert(d.error || 'Failed');
      btn.disabled = false;
      btn.textContent = 'Place Order';
    }
  })
  .catch(() => {
    alert('Network error');
    btn.disabled = false;
    btn.textContent = 'Place Order';
  });
}

filterProducts();
</script>
</body>
</html>
"""

ADMIN_HTML = r"""
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin - Tracking</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, sans-serif; background: #f5f5f7; margin: 0; padding: 16px; }
.container { max-width: 600px; margin: 0 auto; }
h1 { font-size: 20px; }
.card { background: #fff; border-radius: 10px; padding: 14px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card .id { font-weight: 600; color: #0071e3; }
.card .meta { font-size: 13px; color: #666; margin: 4px 0; }
input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; margin-top: 6px; }
button { margin-top: 6px; padding: 8px 16px; background: #0071e3; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.done { opacity: .6; }
</style>
</head>
<body>
<div class="container">
  <h1>Pending Orders</h1>
  <div id="list">Loading...</div>
</div>
<script>
function load() {
  fetch('/api/orders').then(r => r.json()).then(orders => {
    const box = document.getElementById('list');
    const pending = Object.entries(orders).filter(([id, o]) => o.status !== 'shipped');
    if (!pending.length) { box.innerHTML = '<p>No pending orders</p>'; return; }
    box.innerHTML = pending.map(([id, o]) => `
      <div class="card" id="card-${id}">
        <div class="id">${id}</div>
        <div class="meta">${o.warehouse || ''} | $${o.total} | ${o.network}</div>
        <div class="meta">${(o.items || []).map(i => i.name + ' x' + i.qty).join(', ')}</div>
        <div class="meta">${o.address || ''}</div>
        <div class="meta">TG: ${o.tg_user || '-'}</div>
        <input id="track-${id}" placeholder="Tracking number">
        <button onclick="ship('${id}')">Send Tracking</button>
      </div>
    `).join('');
  });
}
function ship(id) {
  const tracking = document.getElementById('track-' + id).value.trim();
  if (!tracking) { alert('Enter tracking'); return; }
  fetch('/api/track', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order_id: id, tracking: tracking }),
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      document.getElementById('card-' + id).classList.add('done');
      alert('Sent');
    } else alert(d.error || 'Failed');
  });
}
load();
</script>
</body>
</html>
"""

# ==================== ROUTES ====================
@app.route("/")
def index():
    products = load_products()
    return render_template_string(PAGE_HTML, products=products, pay=PAY_ADDRESSES)

@app.route("/admin")
def admin():
    return render_template_string(ADMIN_HTML)

@app.route("/api/order", methods=["POST"])
def api_order():
    data = request.get_json(force=True)
    items = data.get("items") or []
    if not items:
        return jsonify(ok=False, error="No items")
    address = (data.get("address") or "").strip()
    if len(address) < 10:
        return jsonify(ok=False, error="Address too short")

    order_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    order = {
        "order_id": order_id,
        "warehouse": data.get("warehouse", "us"),
        "network": data.get("network", "BTC"),
        "items": items,
        "total": data.get("total", 0),
        "address": address,
        "tg_user": data.get("tg_user", ""),
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }
    orders = load_orders()
    orders[order_id] = order
    save_orders(orders)

    # Telegram notify
    wh = WAREHOUSES.get(order["warehouse"], order["warehouse"])
    lines = [f"• {i['name']} × {i['qty']}" for i in items]
    text = (
        f"🆕 <b>新网页订单</b>\n"
        f"订单：<code>{order_id}</code>\n"
        f"仓库：{wh}\n"
        f"金额：${order['total']} ({order['network']})\n"
        f"商品：\n" + "\n".join(lines) + "\n"
        f"地址：{address}\n"
        f"客户TG：{order['tg_user'] or '未填'}"
    )
    send_tg(ADMIN_CHAT_ID, text)

    return jsonify(ok=True, order_id=order_id)

@app.route("/api/orders")
def api_orders():
    return jsonify(load_orders())

@app.route("/api/track", methods=["POST"])
def api_track():
    data = request.get_json(force=True)
    order_id = data.get("order_id", "").strip()
    tracking = data.get("tracking", "").strip()
    if not order_id or not tracking:
        return jsonify(ok=False, error="Missing fields")

    orders = load_orders()
    order = orders.get(order_id)
    if not order:
        return jsonify(ok=False, error="Order not found")

    order["tracking"] = tracking
    order["status"] = "shipped"
    order["shipped_at"] = datetime.utcnow().isoformat()
    orders[order_id] = order
    save_orders(orders)

    # Notify admin
    send_tg(ADMIN_CHAT_ID, f"📦 订单 <code>{order_id}</code> 已发货\n跟踪号：<code>{tracking}</code>")

    # If customer left TG username, try to notify (optional, needs their chat id stored)
    # For now just admin gets it; customer can be told manually or via their username search

    return jsonify(ok=True)

@app.route("/health")
def health():
    return "ok"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
