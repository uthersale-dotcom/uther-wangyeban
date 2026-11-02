#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Uther Web Order — single file for Railway.

Deploy:
  1. Open this repo on GitHub.
  2. Replace the whole app.py with this file. Commit.
  3. Railway redeploys automatically.
  4. Optional env: BOT_TOKEN, DESK_KEY, DATA_DIR.
  5. Add a Railway Volume mounted at /data so orders survive restarts.

Customer flow:
  - Pay creates an order code immediately.
  - Same code returns to payment, then address, then track.
  - Address is locked until the transaction id is verified.
  - Staff use /desk (key desk-uther) and Telegram notifications.
  - This app only sends Telegram messages. It does not poll, so it will not
    kick the Railway order bot.
"""

import html
import json
import os
import random
import re
import string
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from flask import Flask, redirect, render_template_string, request, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "uther-web-order-desk")

BOT_TOKEN = (os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN") or "8501248532:AAG_dJrb3hETUlMZ3v7NQlkh2lH9tHX-C88").strip()
DESK_KEY = os.environ.get("DESK_KEY", "desk-uther").strip()
US_ADMINS = [5922115358, 8329090358]
INTL_ADMINS = [5922115358, 8329090358, 8615921011]

LOCK_MINUTES = 45
LATE_HOURS = 24
FAIL_LIMIT = 5

PAY = {
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
}
PAY["au"] = PAY["eu"]
PAY["ca"] = PAY["eu"]
PAY["uk"] = PAY["eu"]

WAREHOUSES = [
    ("us", "USA"),
    ("eu", "Europe"),
    ("au", "Australia"),
    ("ca", "Canada"),
    ("uk", "UK"),
]

USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SOL_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo": "PYUSD",
}
US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA",
    "ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK",
    "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC",
}

def P(key, name, wh, tiers, stock=None):
    item = {"key": key, "name": name, "warehouse": wh, "tiers": {str(k): float(v) for k, v in tiers.items()}}
    if stock is not None:
        item["stock"] = stock
    return item

EMBEDDED = [
    P("re60", "RETA 60mg RE60-0814", "us", {"1": 276, "2": 266, "3": 256}),
    P("re50", "reta 50mg US-RE50-0730", "us", {"1": 248, "2": 238, "3": 228}),
    P("re40", "RETATRUTIDE 40mg re40-0719", "us", {"1": 218, "2": 208, "3": 198}),
    P("re30", "RETA 30mg RE30-0822", "us", {"1": 189, "2": 179, "3": 169}),
    P("re20", "reta 20mg USRE20-0906", "us", {"1": 148, "2": 138, "3": 128}),
    P("re15", "reta 15mg USRE15-0903", "us", {"1": 143, "2": 128, "3": 118}),
    P("RE10", "RETA 10MG RE10-0729", "us", {"1": 138, "2": 128, "3": 118}),
    P("ze60", "TIRZEPATIDE 60mg ze60-0724", "us", {"1": 180, "2": 170, "3": 160}),
    P("us_ze30", "tirze 30mg USZE30-0903", "us", {"1": 128, "2": 118, "3": 108}),
    P("ze10", "tirze 10mg USZE10-0910", "us", {"1": 69, "2": 59, "3": 49}),
    P("mito120", "MITOPRIME 120mg MITO120-0802", "us", {"1": 138, "2": 128, "3": 118}),
    P("nask", "NA SELANK AMIDATE 10MG", "us", {"1": 80, "2": 75, "3": 70}),
    P("nasx", "NA SEMAX AMIDATE 10MG", "us", {"1": 80, "2": 75, "3": 70}),
    P("car40", "CARTALAX 40MG CARTA40-0730", "us", {"1": 248, "2": 238, "3": 228}),
    P("GHK100", "GHK-Cu 100mg", "us", {"1": 79, "2": 69, "3": 59}, 0),
    P("klow", "klow KLO80-0815", "us", {"1": 208, "2": 198, "3": 188}),
    P("glow", "glow GLO70-0807", "us", {"1": 178, "2": 168, "3": 158}),
    P("core45", "Coremend CORE45-0822", "us", {"1": 378, "2": 368, "3": 358}),
    P("ss50", "ss31-50mg SS50-0815", "us", {"1": 278, "2": 268, "3": 258}),
    P("mo40", "MOTS-C 40MG", "us", {"1": 188, "2": 178, "3": 168}),
    P("ao5", "AOD 9604 5mg and HAC water", "us", {"1": 198, "2": 188, "3": 178}),
    P("tbbp1010", "bpc157 10mg + tb4 10mg", "us", {"1": 158, "2": 148, "3": 138}, 0),
    P("bp10", "bpc-157 10mg", "us", {"1": 78, "2": 68, "3": 58}),
    P("te20", "tesamorelin 20mg", "us", {"1": 278, "2": 268, "3": 258}),
    P("tb4", "TB4 10mg", "us", {"1": 138, "2": 128, "3": 108}),
    P("ti103", "tesamorelin 10mg + ipamorelin 3mg", "us", {"1": 178, "2": 168, "3": 158}),
    P("te10", "tesamorelin 10mg", "us", {"1": 156, "2": 146, "3": 126}, 0),
    P("ip10", "ipamorelin 10mg", "us", {"1": 118, "2": 108, "3": 98}),
    P("ci20", "CJC NO dac/ipa 10/10", "us", {"1": 167, "2": 157, "3": 147}),
    P("naep20", "N acetyl epitalon 20mg", "us", {"1": 128, "2": 118, "3": 108}),
    P("ctb", "cart 10mg + tb4 5mg + bp 5mg", "us", {"1": 138, "2": 128, "3": 118}, 0),
    P("na1000", "NAD 1000mg", "us", {"1": 158, "2": 148, "3": 138}),
    P("na500", "NAD 500mg", "us", {"1": 105, "2": 95, "3": 85}),
    P("glu1500", "Glutathione 1500mg", "us", {"1": 139, "2": 129, "3": 119}),
    P("kpv", "kpv 30mg", "us", {"1": 138, "2": 128, "3": 118}),
    P("gk", "GHK50mg/KPV20mg", "us", {"1": 138, "2": 128, "3": 118}),
    P("ta1", "THYMOSIN ALPHA-1 10mg", "us", {"1": 158, "2": 148, "3": 138}),
    P("mt2", "MT2 10mg", "us", {"1": 65, "2": 55, "3": 50}),
    P("pt141", "PT-141 10MG", "us", {"1": 86, "2": 76, "3": 66}),
    P("51q", "5-AMINO-1MQ 50MG", "us", {"1": 118, "2": 108, "3": 98}),
    P("Survodutide", "Survodutide 10mg", "us", {"1": 150}),
    P("bac", "BAC 10mlx10vials", "us", {"1": 55}),
    P("p21", "p21 10mg", "us", {"1": 268, "2": 248, "3": 220}),
    P("ada", "adamax 10mg", "us", {"1": 248, "2": 238, "3": 228}),
    P("illu", "ILLUMINEURO ILLUM-0724", "us", {"1": 248, "2": 238, "3": 228}),
    P("ahkraw", "AHK-cu raws 10g", "us", {"1": 138}, 57),
    P("ghkraw", "GHK-Cu raws 10g", "us", {"1": 78}, 25),
    P("bioregulators", "Bio Regulators Special (10 vials)", "us", {"1": 180}, 39),
    P("sr10", "sermorelin 10mg", "us", {"1": 146, "2": 136, "3": 126}),
    P("ll5", "LL37 5mg", "us", {"1": 126, "2": 116, "3": 106}),
    P("fox", "FOX04-DRI 10mg", "us", {"1": 278, "2": 268, "3": 258}),
    P("eu_bac", "BAC 3ml", "eu", {"1": 40}),
    P("eu_RE10", "Retatrutide 10mg", "eu", {"1": 138, "2": 128, "3": 118}),
    P("eu_re20", "Reta 20mg", "eu", {"1": 148, "2": 138, "3": 128}),
    P("re24", "reta 24mg", "eu", {"1": 168, "2": 158, "3": 148}),
    P("eu_re30", "RETA 30mg", "eu", {"1": 189, "2": 179, "3": 169}),
    P("eu_ze10", "Tirzepatide 10mg", "eu", {"1": 69, "2": 59, "3": 49}),
    P("tirz30", "Tirzepatide 30mg", "eu", {"1": 128, "2": 118, "3": 108}),
    P("eu_ze60", "Tirzepatide 60mg", "eu", {"1": 180, "2": 170, "3": 160}),
    P("eu_klow", "KLOW 80mg", "eu", {"1": 208, "2": 198, "3": 188}),
    P("eu_mito120", "MitoPrime 120mg", "eu", {"1": 138, "2": 128, "3": 118}),
    P("eu_tbbp1010", "TB4 10mg BPC 10mg mix", "eu", {"1": 158, "2": 148, "3": 138}),
    P("ds10", "DSIP 10mg", "eu", {"1": 95}),
    P("eu_ci20", "CJC No DAC / Ipamorelin 10/10mg", "eu", {"1": 167, "2": 157, "3": 147}),
    P("eu_ctb", "Cartalax + TB4 + BPC", "eu", {"1": 138, "2": 128, "3": 118}),
    P("eu_51q", "5-Amino-1MQ 50mg", "eu", {"1": 118, "2": 108, "3": 98}),
    P("eu_ip10", "Ipamorelin 10mg", "eu", {"1": 118, "2": 108, "3": 98}),
    P("hk100", "GHK-Cu 100mg", "eu", {"1": 79, "2": 69, "3": 59}),
    P("eu_ao5", "AOD-9604 5mg and HAC 3ml", "eu", {"1": 178, "2": 168, "3": 158}),
    P("eu_glu1500", "GMP Glutathione 1500mg", "eu", {"1": 139, "2": 129, "3": 119}),
    P("au_re10", "reta 10mg", "au", {"1": 120}, 0),
    P("au_k100", "GHK-CU 100mg", "au", {"1": 90}),
    P("au_re20", "RETA 20mg", "au", {"1": 160}, 0),
    P("au_ze10", "Tirz 10mg", "au", {"1": 88}),
    P("ca_re10", "reta 10mg", "ca", {"1": 140}),
    P("ca_k100", "GHK-cu 100mg", "ca", {"1": 90}),
    P("ca_klow", "KLOW", "ca", {"1": 260}),
    P("ca_re20", "reta 20mg", "ca", {"1": 180}),
    P("ca_ze10", "tirze 10mg", "ca", {"1": 90}),
    P("tb1010", "TB4/BP157 10/10", "ca", {"1": 170}),
    P("uk_re20", "reta 20mg", "uk", {"1": 148, "2": 138, "3": 128}),
    P("uk_re30", "reta 30mg", "uk", {"1": 189, "2": 179, "3": 169}),
    P("uk_ze30", "tirze 30mg", "uk", {"1": 128, "2": 118, "3": 108}),
    P("uk_ze60", "tirze 60mg", "uk", {"1": 180, "2": 170, "3": 160}),
    P("ci1010", "cjc no dac + ipamorelin", "uk", {"1": 167, "2": 157, "3": 147}),
    P("uk_k100", "GHK-CU 100mg", "uk", {"1": 79, "2": 69, "3": 59}),
    P("uk_klow", "KLOW 80mg", "uk", {"1": 208, "2": 198, "3": 188}),
    P("51Q", "5-Amino-1MQ 50mg", "uk", {"1": 118, "2": 108, "3": 98}),
    P("uk_glu1500", "GMP Glutathione 1500mg", "uk", {"1": 139, "2": 129, "3": 119}),
    P("uk_ao5", "AOD-9604 5mg with HAC 3ml", "uk", {"1": 178, "2": 168, "3": 158}),
]

_lock = threading.Lock()
_fails = {}


def data_dir():
    preferred = Path(os.environ.get("DATA_DIR", "/data"))
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return preferred
    except Exception:
        fallback = Path(__file__).resolve().parent / "data"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DATA = data_dir()
DB_PATH = DATA / "web_orders.json"


def now():
    return datetime.now(timezone.utc)


def parse_time(value):
    return datetime.fromisoformat(value)


def load_db():
    if DB_PATH.exists():
        try:
            data = json.loads(DB_PATH.read_text(encoding="utf-8"))
            data.setdefault("orders", {})
            data.setdefault("used_txids", {})
            return data
        except Exception:
            pass
    return {"orders": {}, "used_txids": {}}


def save_db(data):
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_PATH)


def products():
    path = DATA / "products.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            out = []
            for key, value in raw.items():
                tiers = value.get("price_tiers") or {"1": value.get("price", 0)}
                item = {
                    "key": key,
                    "name": value.get("name", key),
                    "warehouse": value.get("warehouse", "us"),
                    "tiers": {str(k): float(v) for k, v in tiers.items()},
                }
                if "stock" in value:
                    item["stock"] = value["stock"]
                out.append(item)
            if out:
                return out
        except Exception:
            pass
    return EMBEDDED


def product_by_key(key):
    for item in products():
        if item["key"] == key:
            return item
    return None


def unit_price(product, qty):
    tiers = [int(k) for k in product["tiers"] if int(k) <= qty]
    pick = str(max(tiers) if tiers else 1)
    return float(product["tiers"].get(pick) or next(iter(product["tiers"].values())))


def tier_label(product):
    a = product["tiers"].get("1", next(iter(product["tiers"].values())))
    b = product["tiers"].get("2", a)
    c = product["tiers"].get("3", b)
    if b == a and c == a:
        return f"${a:g}"
    return f"1 ${a:g} · 2 ${b:g} · 3+ ${c:g}"


def new_id(existing):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        code = "U" + "".join(random.choice(alphabet) for _ in range(7))
        if code not in existing:
            return code
    raise RuntimeError("could not make an order code")


def btc_price():
    try:
        data = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=8).json()
        price = float(data["data"]["amount"])
        if price > 1000:
            return price
    except Exception:
        pass
    try:
        data = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=8).json()
        price = float(data["price"])
        if price > 1000:
            return price
    except Exception:
        pass
    return 63000.0


def unique_amount(network, total, orders):
    for _ in range(8):
        if network == "BTC":
            noise = random.randint(10, 99) / 1e8
            amount = f"{round(total / btc_price() + noise, 8):.8f}"
            unit = "BTC"
        else:
            amount = f"{round(total + random.randint(10, 99) / 100, 2):.2f}"
            unit = "USDC" if network == "SOL" else "USDT"
        clash = any(
            row["unique_amount"] == amount and row["network"] == network and row["status"] == "waiting_payment"
            and parse_time(row["expires"]) > now()
            for row in orders.values()
        )
        if not clash:
            return amount, unit
    raise RuntimeError("could not reserve a unique amount")


def support_for(wh):
    return "https://t.me/UtherTuralyon" if wh == "us" else "https://t.me/Uthertirion"


def address_guide(wh):
    if wh == "uk":
        return "Full name, street and flat, city, UK postcode (SW1A 1AA), country, and a phone that includes +44. One item per line."
    if wh == "us":
        return "English letters only. PO Boxes are not accepted. Full name, street, City ST ZIP, and a phone number."
    if wh == "au":
        return "Full name, street, suburb, state, postcode, and phone. One item per line."
    if wh == "ca":
        return "Full name, street, city, province, postal code, and phone. One item per line."
    return "Full name, street, city, postal code, country, and phone with country code. One item per line."


def validate_address(text, wh):
    raw = (text or "").strip()
    if wh == "us":
        if len(raw) < 20:
            return "Address is too short."
        if re.search(r"[^\x00-\x7F]", raw):
            return "Use English letters, numbers, and basic symbols only."
        if re.search(r"\b(p\.?\s*o\.?\s*box|post\s*office\s*box|pobox)\b", raw, re.I):
            return "PO Box addresses are not accepted. Send a real street address."
        if not re.search(r"\b\d{5}(?:-\d{4})?\b", raw):
            return "Missing a US ZIP code."
        if not any(code in US_STATES for code in re.findall(r"\b([A-Za-z]{2})\b", raw)):
            return "Missing a US state code, for example CA."
        if len(re.sub(r"\D", "", raw)) < 10:
            return "Missing a phone number."
        return ""
    if len(raw) < 15:
        return "Address is too short."
    if re.search(r"[\u4e00-\u9fff\u0400-\u04ff]", raw):
        return "Use Latin letters only."
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) < 3:
        return "Use at least 3 lines: name, street, city and phone."
    if re.fullmatch(r"[\d\s-]+", lines[0]):
        return "The first line should be the recipient name."
    if len(re.sub(r"\D", "", raw)) < 8:
        return "Missing a phone number."
    if wh == "uk":
        if not re.search(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", raw, re.I):
            return "Missing a UK postcode, for example SW1A 1AA."
        if "+44" not in raw.replace(" ", ""):
            return "Phone must include +44."
    return ""


def notify(warehouse, text):
    if not BOT_TOKEN:
        return
    ids = US_ADMINS if warehouse == "us" else INTL_ADMINS
    for chat_id in ids:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text[:3900]},
                timeout=12,
            )
        except Exception:
            pass


def verify_btc(txid, address, expected):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", txid):
        return False, "Invalid BTC transaction id.", ""
    try:
        response = requests.get(f"https://mempool.space/api/tx/{txid.lower()}", timeout=15)
        if response.status_code == 404:
            return False, "Transaction not found yet. Wait a minute and try again.", ""
        response.raise_for_status()
        data = response.json()
    except Exception:
        return False, "Bitcoin lookup failed. Try again in a minute.", ""
    paid = 0.0
    found = False
    for out in data.get("vout") or []:
        if (out.get("scriptpubkey_address") or "").lower() == address.lower():
            found = True
            paid += (out.get("value") or 0) / 1e8
    if not found:
        return False, "This transaction did not pay our Bitcoin address.", ""
    if paid + 1e-8 < expected * 0.98:
        return False, f"Amount too low. Expected about {expected:.8f} BTC, received {paid:.8f}.", ""
    if paid > expected * 1.02 + 1e-8:
        return False, f"Amount does not match. Expected about {expected:.8f} BTC, received {paid:.8f}.", ""
    state = "confirmed" if (data.get("status") or {}).get("confirmed") else "seen, waiting for confirmation"
    return True, f"Verified {paid:.8f} BTC ({state}).", "BTC"


def verify_erc20(txid, address, expected):
    hashed = txid if txid.startswith("0x") else "0x" + txid
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", hashed):
        return False, "Invalid Ethereum transaction id.", ""
    body = {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [hashed]}
    receipt = None
    for rpc in ("https://ethereum-rpc.publicnode.com", "https://rpc.ankr.com/eth", "https://eth.llamarpc.com"):
        try:
            payload = requests.post(rpc, json=body, timeout=12).json()
            if payload.get("result"):
                receipt = payload["result"]
                break
        except Exception:
            continue
    if not receipt:
        return False, "Transaction not found yet. Wait and submit the same id again.", ""
    if receipt.get("status") == "0x0":
        return False, "This transaction failed on Ethereum.", ""
    padded = "0x" + address[2:].lower().zfill(64)
    amount = 0.0
    token = ""
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if not topics or topics[0].lower() != TRANSFER:
            continue
        if len(topics) < 3 or topics[2].lower() != padded:
            continue
        contract = (log.get("address") or "").lower()
        if contract not in (USDT.lower(), USDC.lower()):
            continue
        amount += int(log.get("data") or "0x0", 16) / 1_000_000
        token = "USDT" if contract == USDT.lower() else "USDC"
    if not token:
        return False, "No USDT or USDC transfer to our address in this transaction.", ""
    if amount < expected * 0.995:
        return False, f"Amount too low. Expected about ${expected:.2f}, received ${amount:.2f} {token}.", ""
    if amount > expected * 1.02:
        return False, f"Amount does not match. Expected about ${expected:.2f}, received ${amount:.2f} {token}.", ""
    return True, f"Verified ${amount:.2f} {token}.", token


def verify_sol(txid, address, expected):
    if not 64 <= len(txid) <= 100:
        return False, "Invalid Solana transaction id.", ""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [txid, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}],
    }
    data = None
    for rpc in ("https://api.mainnet-beta.solana.com", "https://solana-rpc.publicnode.com"):
        try:
            payload = requests.post(rpc, json=body, timeout=15).json()
            if payload.get("result"):
                data = payload["result"]
                break
        except Exception:
            continue
    if not data:
        return False, "Transaction not found yet. Wait and submit the same id again.", ""
    meta = data.get("meta") or {}
    if meta.get("err") is not None:
        return False, "This Solana transaction failed.", ""

    def mapped(rows):
        out = {}
        for row in rows or []:
            ui = ((row.get("uiTokenAmount") or {}).get("uiAmount"))
            if row.get("owner") and row.get("mint") and ui is not None:
                out[f"{row['owner']}|{row['mint']}"] = float(ui)
        return out

    pre = mapped(meta.get("preTokenBalances"))
    post = mapped(meta.get("postTokenBalances"))
    matched = 0.0
    token = ""
    for key in set(pre) | set(post):
        owner, mint = key.split("|", 1)
        if owner != address or mint not in SOL_MINTS:
            continue
        delta = post.get(key, 0) - pre.get(key, 0)
        if delta > 0:
            matched += delta
            token = SOL_MINTS[mint]
    if not token:
        return False, "No USDC, USDT, or PYUSD arrived at our Solana address.", ""
    if matched < expected * 0.995:
        return False, f"Amount too low. Expected about ${expected:.2f}, received ${matched:.2f} {token}.", ""
    if matched > expected * 1.02:
        return False, f"Amount does not match. Expected about ${expected:.2f}, received ${matched:.2f} {token}.", ""
    return True, f"Verified ${matched:.2f} {token} on Solana.", token


def window_open(order):
    return now() <= parse_time(order["expires"]) + timedelta(hours=LATE_HOURS)


STYLE = """
<style>
*{box-sizing:border-box}
body{margin:0;background:#f3efe6;color:#1c1915;font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:520px;margin:0 auto;padding:20px 16px 48px}
a{color:#8c4a2f;text-decoration:none}
h1{font-size:30px;margin:4px 0 16px}
.top{display:flex;justify-content:space-between;align-items:end}
.card{background:#fffdf8;border:1px solid #e4dccf;border-radius:16px;padding:14px;margin:0 0 12px}
.muted{color:#6f675c;font-size:14px}
.row{display:flex;justify-content:space-between;gap:12px;align-items:center}
.pills{display:flex;gap:8px;overflow:auto;margin-bottom:12px}
.pills a{border:1px solid #e4dccf;background:#fffdf8;border-radius:999px;padding:8px 14px;color:#1c1915;white-space:nowrap;flex:0 0 auto}
.pills .on{background:#1c1915;color:#f3efe6}
input,textarea,select,button{font:inherit}
input[type=text],input[type=password],input[type=number],textarea,select{width:100%;border:1px solid #e4dccf;border-radius:12px;padding:12px;background:#fff}
input[type=radio]{width:auto;margin:0}
textarea{min-height:120px}
button,.btn{border:0;border-radius:12px;background:#8c4a2f;color:#fff8f3;padding:12px 16px;font-weight:600}
input.qty{width:72px}
.nets{display:flex;flex-direction:column;gap:8px;margin-top:8px}
.nets label{display:flex;align-items:center;gap:10px;border:1px solid #e4dccf;background:#fffdf8;border-radius:12px;padding:12px 14px}
.nets label:has(input:checked){background:#1c1915;color:#f3efe6;border-color:#1c1915}
.bad{background:#f8e6e4;color:#8f2d2d;border-radius:12px;padding:12px}
.good{background:#e7f4ec;color:#1f6b45;border-radius:12px;padding:12px}
.warn{background:#f8efd8;color:#8a5a12;border-radius:12px;padding:12px}
code,input.copy{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}
.small{font-size:13px}
</style>
"""

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>""" + STYLE + """</head><body><main>{{ body|safe }}</main></body></html>"""


def page(title, body):
    return render_template_string(PAGE, title=title, body=body)


@app.get("/health")
def health():
    return "ok"


@app.get("/")
def shop():
    wh = request.args.get("wh", "us")
    if wh not in PAY:
        wh = "us"
    items = [item for item in products() if item["warehouse"] == wh]
    pills = "".join(
        f'<a class="{"on" if code == wh else ""}" href="/?wh={code}">{label}</a>'
        for code, label in WAREHOUSES
    )
    rows = []
    for item in items:
        sold = item.get("stock") == 0
        stock = ""
        if sold:
            stock = '<div class="small muted">Sold out</div>'
        elif item.get("stock"):
            stock = f'<div class="small muted">{item["stock"]} left</div>'
        disabled = "disabled" if sold else ""
        rows.append(
            f'<div class="card"><div class="row"><div><b>{html.escape(item["name"])}</b>'
            f'<div class="small" style="color:#8c4a2f">{html.escape(tier_label(item))}</div>{stock}</div>'
            f'<input class="qty" type="number" min="0" max="50" name="qty_{html.escape(item["key"])}" value="0" {disabled}></div></div>'
        )
    body = f"""
    <div class="top"><div><div class="small muted">UTHER</div><h1>Order desk</h1></div><a href="/track">Track</a></div>
    <div id="recent"></div>
    <div class="card"><div class="muted">Already started an order?</div>
      <form action="/track" method="get" class="row" style="margin-top:8px">
        <input name="id" placeholder="Order code" style="text-transform:uppercase">
        <button>Open</button>
      </form>
    </div>
    <div class="pills">{pills}</div>
    <form method="post" action="/checkout">
      <input type="hidden" name="warehouse" value="{wh}">
      {''.join(rows)}
      <div class="card">
        <div class="muted">Payment network</div>
        <div class="nets">
          <label><input type="radio" name="network" value="ERC20" checked><span>USDT / USDC (Ethereum)</span></label>
          <label><input type="radio" name="network" value="SOL"><span>USDC (Solana)</span></label>
          <label><input type="radio" name="network" value="BTC"><span>Bitcoin</span></label>
        </div>
      </div>
      <button style="width:100%">Pay</button>
    </form>
    <script>
    const key="uther-orders";
    try {{
      const ids=JSON.parse(localStorage.getItem(key)||"[]");
      if(Array.isArray(ids)&&ids.length){{
    document.getElementById("recent").innerHTML=ids.slice(0,8).map(function(id){{
      return '<div class="card" style="display:flex;justify-content:space-between;align-items:center"><a href="/order/'+id+'">Continue order <b>'+id+'</b></a><button type="button" data-id="'+id+'" onclick="var k=\'uther-orders\';var a=JSON.parse(localStorage.getItem(k)||\'[]\').filter(function(x){{return x!==this.getAttribute(\'data-id\')}}.bind(this));localStorage.setItem(k,JSON.stringify(a));this.parentElement.remove()" style="background:none;border:0;color:#8c4a2f;font-size:20px;padding:0 8px">×</button></div>';
    }}).join("");
      }}
    }} catch(e) {{}}
    </script>
    """
    return page("Uther Order", body)


@app.post("/checkout")
def checkout():
    wh = request.form.get("warehouse", "us")
    network = request.form.get("network", "ERC20")
    if wh not in PAY or network not in ("BTC", "ERC20", "SOL"):
        return page("Order", '<p class="bad">Choose a warehouse and network.</p><a href="/">Back</a>'), 400
    lines = []
    catalog = {item["key"]: item for item in products()}
    for key, product in catalog.items():
        if product["warehouse"] != wh:
            continue
        try:
            qty = int(request.form.get(f"qty_{key}") or 0)
        except ValueError:
            qty = 0
        if qty <= 0:
            continue
        if product.get("stock") == 0:
            return page("Order", f'<p class="bad">{html.escape(product["name"])} is sold out.</p><a href="/">Back</a>'), 400
        unit = unit_price(product, qty)
        lines.append({
            "key": key,
            "name": product["name"],
            "qty": qty,
            "unit": round(unit, 2),
            "line": round(unit * qty, 2),
        })
    if not lines:
        return page("Order", '<p class="bad">Select at least one product.</p><a href="/">Back</a>'), 400
    total = round(sum(line["line"] for line in lines), 2)
    with _lock:
        db = load_db()
        try:
            amount, unit = unique_amount(network, total, db["orders"])
        except Exception:
            return page("Order", '<p class="bad">Could not reserve a payment amount. Try again.</p><a href="/">Back</a>'), 500
        code = new_id(db["orders"])
        created = now()
        order = {
            "id": code,
            "warehouse": wh,
            "network": network,
            "items": lines,
            "total": total,
            "unique_amount": amount,
            "amount_unit": unit,
            "pay_address": PAY[wh][network],
            "status": "waiting_payment",
            "txid": "",
            "verify_msg": "",
            "token": "",
            "address": "",
            "tracking": "",
            "created": created.isoformat(),
            "expires": (created + timedelta(minutes=LOCK_MINUTES)).isoformat(),
        }
        db["orders"][code] = order
        save_db(db)
    notify(wh, f"新网页订单 {code}\n{wh.upper()} · {network}\n应付 {amount} {unit}\n客户用这个订单号可以回到付款页。")
    return redirect(url_for("order_page", code=code))


def order_body(order, error=""):
    items = "".join(
        f'<div class="row"><span>{html.escape(item["name"])} × {item["qty"]}</span><b>${item["line"]:.2f}</b></div>'
        for item in order["items"]
    )
    err = f'<p class="bad">{html.escape(error)}</p>' if error else ""
    head = f"""
    <div class="top"><a href="/">Shop</a><b>{order["id"]}</b></div>
    <div class="card"><div class="small muted">Order code</div><div style="font-size:28px">{order["id"]}</div>
      <div class="muted">Keep this code. If the page closes, open Track and enter it.</div></div>
    <div class="card">{items}<div class="row" style="margin-top:8px"><span>Catalog total</span><b>${order["total"]:.2f}</b></div></div>
    {err}
    """
    if order["status"] == "waiting_payment":
        if not window_open(order):
            return head + '<p class="bad">This payment window has closed. If you already paid, contact support and do not pay again.</p>'
        return head + f"""
        <div class="warn">The price lock lasts 45 minutes. You can still submit the transaction id for 24 hours after that. Do not pay a second time. Come back with code {order["id"]}.</div>
        <div class="card"><div class="small muted">Send exactly</div>
          <div style="font-size:24px">{order["unique_amount"]} {order["amount_unit"]}</div>
          <div class="small muted" style="margin-top:10px">{order["network"]}</div>
          <div id="payaddr" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all;background:#fff;border:1px solid #e4dccf;border-radius:12px;padding:12px;margin-top:8px">{html.escape(order["pay_address"])}</div>
          <button type="button" style="width:100%;margin-top:8px" onclick="navigator.clipboard.writeText(document.getElementById('payaddr').textContent);this.textContent='Copied'">Copy address</button>
        </div>
        <form method="post" action="/order/{order["id"]}/txid">
          <textarea name="txid" placeholder="Paste the transaction id"></textarea>
          <button style="width:100%;margin-top:8px">Submit transaction id</button>
        </form>
        <p><a href="{support_for(order["warehouse"])}">Contact support</a></p>
        """
    if order["status"] == "waiting_address":
        warn = ""
        if order["warehouse"] == "us":
            warn = '<div class="warn">PO Box addresses are not accepted. Send a real street address.</div>'
        return head + f"""
        <div class="good">{html.escape(order.get("verify_msg") or "Payment verified.")} Address is next. It was locked until this step.</div>
        {warn}
        <p class="muted">{html.escape(address_guide(order["warehouse"]))}</p>
        <form method="post" action="/order/{order["id"]}/address">
          <textarea name="address" placeholder="Name&#10;Street&#10;City, region, postcode&#10;Phone"></textarea>
          <button style="width:100%;margin-top:8px">Save address</button>
        </form>
        """
    tracking = f'<p><b>{html.escape(order["tracking"])}</b></p>' if order.get("tracking") else ""
    label = "Shipped" if order["status"] == "shipped" else "Address received"
    return head + f"""
    <div class="card"><b>{label}</b>
      <p class="muted">Keep code {order["id"]}. Check it any time on the track page.</p>
      {tracking}
      <a href="/track?id={order["id"]}">Track this order</a>
    </div>
    """


@app.get("/order/<code>")
def order_page(code):
    db = load_db()
    order = db["orders"].get(code.upper())
    if not order:
        return page("Not found", '<h1>Order not found</h1><a href="/track">Look up an order</a>'), 404
    body = order_body(order) + f"""
    <script>
    try {{
      const key="uther-orders";
      const ids=JSON.parse(localStorage.getItem(key)||"[]").filter(id=>id!=="{order["id"]}");
      localStorage.setItem(key, JSON.stringify(["{order["id"]}", ...ids].slice(0,8)));
    }} catch(e) {{}}
    </script>
    """
    return page(order["id"], body)


@app.post("/order/<code>/txid")
def submit_txid(code):
    code = code.upper()
    txid = re.sub(r"\s+", "", request.form.get("txid") or "")
    with _lock:
        db = load_db()
        order = db["orders"].get(code)
        if not order:
            return page("Not found", '<h1>Order not found</h1>'), 404
        if order["status"] != "waiting_payment":
            return redirect(url_for("order_page", code=code))
        if not window_open(order):
            return page(code, order_body(order, "This payment window has closed. If you already paid, do not pay again."))
        recent = [stamp for stamp in _fails.get(code, []) if now().timestamp() - stamp < 600]
        if len(recent) >= FAIL_LIMIT:
            return page(code, order_body(order, "Too many failed checks. Wait 10 minutes, or contact support."))
        if txid.lower() in {key.lower(): value for key, value in db["used_txids"].items()}:
            return page(code, order_body(order, "This transaction id was already used."))
        expected = float(order["unique_amount"])
        if order["network"] == "BTC":
            ok, message, token = verify_btc(txid.lower(), order["pay_address"], expected)
        elif order["network"] == "ERC20":
            ok, message, token = verify_erc20(txid.lower(), order["pay_address"], expected)
        else:
            ok, message, token = verify_sol(txid, order["pay_address"], expected)
        if not ok:
            recent.append(now().timestamp())
            _fails[code] = recent
            return page(code, order_body(order, message) + f'<p><a href="{support_for(order["warehouse"])}">Contact support on Telegram</a></p>')
        db["used_txids"][txid] = code
        order.update({"status": "waiting_address", "txid": txid, "verify_msg": message, "token": token})
        save_db(db)
        _fails.pop(code, None)
    notify(order["warehouse"], f"付款已核验 {code}\n{order['warehouse'].upper()} · {order['network']} · {order['unique_amount']} {order['amount_unit']}\n{message}\n客户现在才能填地址。页面关了也用同一订单号回来。")
    return redirect(url_for("order_page", code=code))


@app.post("/order/<code>/address")
def submit_address(code):
    code = code.upper()
    address = (request.form.get("address") or "").strip()
    with _lock:
        db = load_db()
        order = db["orders"].get(code)
        if not order:
            return page("Not found", '<h1>Order not found</h1>'), 404
        if order["status"] != "waiting_address":
            return page(code, order_body(order, "Address opens only after the payment is verified."))
        error = validate_address(address, order["warehouse"])
        if error:
            return page(code, order_body(order, error))
        order["address"] = address
        order["status"] = "paid"
        save_db(db)
    items = "\n".join(f"{item['name']} × {item['qty']}" for item in order["items"])
    when = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    notify(order["warehouse"], (
        f"地址已提交 {code}\n"
        f"{when} (UTC+3)\n"
        f"{order['warehouse'].upper()} · {order['network']}\n"
        f"应付 {order['unique_amount']} {order['amount_unit']}\n"
        f"交易号 {order.get('txid') or '人工确认'}\n"
        f"{items}\n\n"
        f"{address}\n\n"
        f"回复这条消息发跟踪号，或到网站 /desk 回传。发货时请录制开箱视频。"
    ))
    return redirect(url_for("order_page", code=code))


@app.route("/track", methods=["GET"])
def track():
    code = (request.args.get("id") or "").strip().upper()
    result = ""
    if code:
        order = load_db()["orders"].get(code)
        if not order:
            result = '<p class="bad">Order not found.</p>'
        else:
            label = {
                "waiting_payment": "Waiting for payment",
                "waiting_address": "Payment checked. Address still needed.",
                "paid": "Preparing shipment",
                "shipped": "Shipped",
            }.get(order["status"], order["status"])
            tracking = f'<p><b>{html.escape(order["tracking"])}</b></p>' if order.get("tracking") else ""
            cont = ""
            if order["status"] in ("waiting_payment", "waiting_address"):
                cont = f'<p><a href="/order/{code}">Continue this order</a></p>'
            result = f'<div class="card"><div class="small muted">{code}</div><h2>{label}</h2>{tracking}{cont}</div>'
    body = f"""
    <a href="/">Shop</a><h1>Track</h1>
    <p class="muted">The order code brings you back. Before payment is checked, you return to the transaction id. After it is checked, you return to the address form. After shipping, the tracking number shows here. Keep your order code to check status anytime.</p>
    <form class="row">
      <input name="id" value="{html.escape(code)}" placeholder="Uxxxxxxx" style="text-transform:uppercase">
      <button>Check</button>
    </form>
    {result}
    """
    return page("Track", body)

def save_products(items):
    raw = {}
    for item in items:
        row = {
            "name": item["name"],
            "warehouse": item["warehouse"],
            "price_tiers": {str(k): float(v) for k, v in item["tiers"].items()},
        }
        if item.get("stock") not in (None, ""):
            row["stock"] = int(item["stock"])
        raw[item["key"]] = row
    (DATA / "products.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

@app.route("/desk/products", methods=["GET", "POST"])
def desk_products():
    if not desk_ok():
        return redirect("/desk")
    note = ""
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save_product":
            key = (request.form.get("key") or "").strip()
            name = (request.form.get("name") or "").strip()
            wh = request.form.get("warehouse", "us")
            try:
                p1 = float(request.form.get("p1") or 0)
                p2 = float(request.form.get("p2") or p1)
                p3 = float(request.form.get("p3") or p2)
            except ValueError:
                p1 = p2 = p3 = 0
            stock_raw = (request.form.get("stock") or "").strip()
            if not key or not name or wh not in PAY or p1 <= 0:
                note = "商品编号、名称、仓库、1件价格必填。"
            else:
                items = products()
                found = False
                for item in items:
                    if item["key"] == key:
                        item.update({"name": name, "warehouse": wh, "tiers": {"1": p1, "2": p2, "3": p3}})
                        if stock_raw:
                            item["stock"] = int(stock_raw)
                        else:
                            item.pop("stock", None)
                        found = True
                if not found:
                    row = {"key": key, "name": name, "warehouse": wh, "tiers": {"1": p1, "2": p2, "3": p3}}
                    if stock_raw:
                        row["stock"] = int(stock_raw)
                    items.append(row)
                save_products(items)
                note = f"商品 {key} 已保存。"
        elif action == "delete_product":
            key = (request.form.get("key") or "").strip()
            items = [item for item in products() if item["key"] != key]
            save_products(items)
            note = f"商品 {key} 已删除。"
    wh_filter = request.args.get("wh", "us")
    if wh_filter not in PAY:
        wh_filter = "us"
    note_html = f'<p class="good">{html.escape(note)}</p>' if note else ""
    body = f"""
    <div class="top"><h1>商品</h1><a href="/desk">返回订单</a></div>
    <div class="pills"><a href="/desk">订单</a><a class="on" href="/desk/products">商品</a></div>
    {note_html}
    <div class="card"><b>添加商品</b>
      <form method="post">
        <input type="hidden" name="action" value="save_product">
        <input name="key" placeholder="编号，例如 re60">
        <input name="name" placeholder="商品名" style="margin-top:8px">
        <select name="warehouse" style="margin-top:8px">{''.join(f'<option value="{code}">{label}</option>' for code, label in WAREHOUSES)}</select>
        <input name="p1" placeholder="1件价" style="margin-top:8px">
        <input name="p2" placeholder="2件价" style="margin-top:8px">
        <input name="p3" placeholder="3件价" style="margin-top:8px">
        <input name="stock" placeholder="库存，可空" style="margin-top:8px">
        <button style="width:100%;margin-top:8px">保存商品</button>
      </form>
    </div>
    <div class="pills">{''.join(f'<a class="{"on" if code == wh_filter else ""}" href="/desk/products?wh={code}">{label}</a>' for code, label in WAREHOUSES)}</div>
    {''.join(
        f'''<div class="card"><b>{html.escape(item["key"])}</b>
        <div>{html.escape(item["name"])}</div>
        <div class="muted">{html.escape(tier_label(item))} · 库存 {item.get("stock", "不限")}</div>
        <form method="post"><input type="hidden" name="action" value="delete_product"><input type="hidden" name="key" value="{html.escape(item["key"])}"><button>删除</button></form></div>'''
        for item in products() if item["warehouse"] == wh_filter
    )}
    """
    return page("商品", body)

def desk_ok():
    return session.get("desk") == DESK_KEY


@app.route("/desk", methods=["GET", "POST"])
def desk():
    if request.method == "POST" and request.form.get("action") == "login":
        if request.form.get("key") == DESK_KEY:
            session["desk"] = DESK_KEY
        else:
            return page("后台", '<h1>后台</h1><p class="bad">钥匙不对。</p><a href="/desk">返回</a>')
    if request.method == "POST" and request.form.get("action") == "logout":
        session.pop("desk", None)
        return redirect("/desk")
    wh_filter = request.args.get("wh", "us")
    if wh_filter not in PAY:
        wh_filter = "us"
    if not desk_ok():
        return page("后台", """
        <h1>后台</h1>
        <p class="muted">客户在网页下单。客服在这里补核、回传跟踪号。</p>
        <form method="post"><input type="hidden" name="action" value="login">
          <input type="password" name="key" placeholder="后台钥匙">
          <button style="width:100%;margin-top:8px">打开</button>
        </form>
        """)
    note = ""
    if request.method == "POST":
        action = request.form.get("action")
        code = (request.form.get("id") or "").upper()
        with _lock:
            db = load_db()
            order = db["orders"].get(code)
            if action == "accept" and order and order["status"] == "waiting_payment":
                order["status"] = "waiting_address"
                order["verify_msg"] = "Accepted by staff"
                save_db(db)
                note = f"{code} 已人工确认。"
            elif action == "track" and order:
                tracking = (request.form.get("tracking") or "").strip()
                if not order.get("address"):
                    note = "还没有地址，不能发货。"
                elif not 4 <= len(tracking) <= 80:
                    note = "跟踪号长度不对。"
                else:
                    order["tracking"] = tracking
                    order["status"] = "shipped"
                    save_db(db)
                    note = f"{code} 跟踪号已保存。"
            elif action == "address" and order:
                if order["status"] == "waiting_payment":
                    note = "付款还没核验，不能写地址。"
                else:
                    error = validate_address(request.form.get("address") or "", order["warehouse"])
                    if error:
                        note = error
                    else:
                        order["address"] = request.form.get("address").strip()
                        if order["status"] == "waiting_address":
                            order["status"] = "paid"
                        save_db(db)
                        note = f"{code} 地址已保存。"
            elif action == "create":
                wh = request.form.get("warehouse", "us")
                network = request.form.get("network", "ERC20")
                key = request.form.get("product")
                try:
                    qty = int(request.form.get("qty") or 1)
                except ValueError:
                    qty = 1
                product = product_by_key(key)
                if not product or product["warehouse"] != wh or network not in PAY[wh]:
                    note = "商品或仓库不对。"
                else:
                    unit = unit_price(product, qty)
                    total = round(unit * qty, 2)
                    amount, money = unique_amount(network, total, db["orders"])
                    code = new_id(db["orders"])
                    created = now()
                    db["orders"][code] = {
                        "id": code, "warehouse": wh, "network": network,
                        "items": [{"key": key, "name": product["name"], "qty": qty, "unit": round(unit, 2), "line": total}],
                        "total": total, "unique_amount": amount, "amount_unit": money,
                        "pay_address": PAY[wh][network], "status": "waiting_payment",
                        "txid": "", "verify_msg": "", "token": "", "address": "", "tracking": "",
                        "created": created.isoformat(),
                        "expires": (created + timedelta(minutes=LOCK_MINUTES)).isoformat(),
                    }
                    save_db(db)
                    note = f"已开单 {code}，应付 {amount} {money}。把订单号发给客户。"
                    notify(wh, f"后台开单 {code}\n应付 {amount} {money}")

                    
    all_orders = sorted(load_db()["orders"].values(), key=lambda row: row["created"], reverse=True)
    pending = [row for row in all_orders if row["warehouse"] == wh_filter and row["status"] in ("waiting_address", "paid")][:40]
    done = [row for row in all_orders if row["warehouse"] == wh_filter and row["status"] == "shipped"][:20]
    cards = []
    for order in pending:
        goods = ", ".join(f'{item["name"]} × {item["qty"]}' for item in order["items"])
        accept = ""
        if order["status"] == "waiting_payment":
            accept = f'<button name="action" value="accept">人工确认付款</button>'
        cards.append(f"""
        <div class="card">
          <b>{order["id"]} · {order["warehouse"].upper()} · {order["status"]}</b>
          <div class="muted">${order["total"]:.2f} · 应付 {order["unique_amount"]} {order["amount_unit"]} · {order["network"]}</div>
          <div>{html.escape(goods)}</div>
          <div class="small muted">交易号 {html.escape(order.get("txid") or "—")}</div>
          <form method="post">
            <input type="hidden" name="id" value="{order["id"]}">
            {f'<textarea name="address" placeholder="地址">{html.escape(order.get("address") or "")}</textarea><div class="row" style="margin-top:8px">{accept}<button name="action" value="address">保存地址</button></div>' if order["status"] == "waiting_address" and not order.get("address") else ""}
            <div class="row" style="margin-top:8px">
              <input name="tracking" value="{html.escape(order.get("tracking") or "")}" placeholder="跟踪号">
              <button name="action" value="track">回传跟踪号</button>
            </div>
          </form>
        </div>
        """)
    choices = "".join(
        f'<option value="{html.escape(item["key"])}">{html.escape(item["warehouse"].upper())} · {html.escape(item["name"])}</option>'
        for item in products()
    )
    note_html = f'<p class="good">{html.escape(note)}</p>' if note else ""
    body = f"""
    <div class="top"><h1>后台</h1><form method="post"><button name="action" value="logout">退出</button></form></div>
    <p class="muted">回传跟踪号。新订单、付款、地址会发到管理员电报。</p>
    <div class="pills"><a href="/desk">订单</a><a href="/desk/products">商品</a></div>
    {note_html}
    <div class="pills">{''.join(f'<a class="{"on" if code == wh_filter else ""}" href="/desk?wh={code}">{label}</a>' for code, label in WAREHOUSES)}</div>
     <div class="card"><b>添加订单</b>
      <form method="post">
        <input type="hidden" name="action" value="create">
        <select name="warehouse">{''.join(f'<option value="{code}">{label}</option>' for code, label in WAREHOUSES)}</select>
        <select name="product" style="margin-top:8px">{choices}</select>
        <div class="row" style="margin-top:8px">
          <input name="qty" type="number" min="1" max="50" value="1">
          <select name="network"><option>ERC20</option><option>SOL</option><option>BTC</option></select>
        </div>
        <button style="width:100%;margin-top:8px">生成订单</button>
      </form>
    </div>
    {''.join(cards)}
    <div class="card"><b>已完成（已回传跟踪号）</b></div>
    {''.join(
        f'''<div class="card"><b>{order["id"]} · {order["status"]}</b>
        <div class="muted">{html.escape(", ".join(f'{item["name"]} × {item["qty"]}' for item in order["items"]))}</div>
        <div>跟踪号 {html.escape(order.get("tracking") or "—")}</div></div>'''
        for order in done
    )}
    """
    return page("后台", body)

def save_tracking(code, tracking):
    tracking = tracking.strip()
    if not 4 <= len(tracking) <= 80:
        return "跟踪号长度不对。"
    with _lock:
        db = load_db()
        order = db["orders"].get(code.upper())
        if not order:
            return "订单不存在。"
        if not order.get("address"):
            return "还没有地址，不能发货。"
        order["tracking"] = tracking
        order["status"] = "shipped"
        save_db(db)
    return f"{code.upper()} 跟踪号已保存：{tracking}"


def handle_staff(msg):
    text = (msg.get("text") or "").strip()
    chat_id = (msg.get("chat") or {}).get("id")
    if not text or chat_id not in set(US_ADMINS + INTL_ADMINS):
        return

    if text.lower() in ("/pending", "/orders"):
        db = load_db()
        rows = [
            f'{row["id"]} · {row["warehouse"].upper()} · {row["status"]} · {row["unique_amount"]} {row["amount_unit"]}'
            for row in sorted(db["orders"].values(), key=lambda x: x["created"], reverse=True)
            if row["status"] in ("waiting_payment", "waiting_address", "paid")
        ][:30]
        reply = "\n".join(rows) if rows else "没有待处理订单。"
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": reply[:3900]},
            timeout=12,
        )
        return
        
    if text.lower().startswith("/track"):
        parts = text.split(maxsplit=2)
        reply = save_tracking(parts[1], parts[2]) if len(parts) >= 3 else "用法：/track 订单号 跟踪号"
    else:
        replied = (msg.get("reply_to_message") or {}).get("text") or ""
        found = re.search(r"\b(U[A-Z0-9]{7})\b", replied)
        reply = save_tracking(found.group(1), text) if found else ""
    if reply:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": reply},
            timeout=12,
        )


def poll_staff():
    offset = 0
    while True:
        try:
            data = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"timeout": 25, "offset": offset, "allowed_updates": '["message"]'},
                timeout=30,
            ).json()
            for upd in data.get("result") or []:
                offset = upd["update_id"] + 1
                handle_staff(upd.get("message") or {})
        except Exception:
            time.sleep(3)


threading.Thread(target=poll_staff, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
