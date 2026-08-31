import logging
import time
import string
import random
import asyncio
import signal
import os
import fcntl
import json
import hmac
import hashlib
from io import BytesIO
from html import escape
from typing import Optional
from datetime import datetime
from telegram import Update, TelegramObject, MessageEntity, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from telegram.error import Conflict, BadRequest, NetworkError, Forbidden, TimedOut, RetryAfter
from telegram.request import HTTPXRequest

import aiohttp as _aiohttp

import database as db

from mst import get_bin_handler as get_bin_lookup_handler

from config import (
    BOT_TOKEN, OWNER_ID, VERSION, DEV_LINK,
    CHANNEL_USERNAME, CHANNEL_LINK, GROUP_LINK, SUPPORT_LINK,
    BOT_LINK, BOT_USERNAME,
    API_TIMEOUT, REFERRAL_CREDITS, LOCK_FILE,
    GATE_URLS, GATE_SITES, PREMIUM_GATES, FORCE_CHANNELS,
    get_bin_info, kb_result,
    tg_emoji, get_plan_emoji_id, get_random_live_emoji,
    E_CARD, E_USER, E_TIME, E_DEV, E_PRO,
    E_LIVE, E_DECLINED, E_ERRORS, E_PROGRESS, E_GATE,
    PLAN_EMOJIS, PRO_EMOJI_ID,
    BTN_ALL_EMOJI_ID, BTN_STOP_EMOJI_ID,
    PROG_GATE_EMOJI_ID, PROG_LIVE_EMOJI_ID, PROG_DEAD_EMOJI_ID,
    PROG_ERRORS_EMOJI_ID, PROG_PROGRESS_EMOJI_ID,
    CARD_EMOJI_ID, USER_EMOJI_ID, TIME_EMOJI_ID,
    DEV_EMOJI_ID, DECLINED_EMOJI_ID,
)
from sh import (
    cmd_sh,
    get_sh_handler, get_me_handler,
    _check_card_with_retry, SITE_RETRIES, SITE_TIMEOUT,
    run_mass_batch, create_msh_session, MSH_SESSIONS,
    cb_msh_result, cb_msh_stop, _load_sites, _load_proxies,
    probe_all_sites, get_working_sites, start_probe_background, stop_probe_background,
    _send_sticker, get_random_live_emoji,
    get_random_charged_emoji, HIT_RESP_EMOJI_ID, PRO_EMOJI_ID,
    CARD_CHK_BTN_EMOJI_ID, BOT_USERNAME_LINK,
    # NEW: Import modern UI emojis
    MODERN_CARD_EMOJI, MODERN_USER_EMOJI, MODERN_TIME_EMOJI,
    MODERN_DEV_EMOJI, MODERN_PRO_EMOJI, MODERN_DECLINED,
    MODERN_LIVE, MODERN_CHARGED, MODERN_GATE, MODERN_PROGRESS,
    MODERN_ERRORS, GLOW_STAR, GLOW_SPARKLE, GLOW_CROWN, GLOW_DIAMOND, GLOW_FIRE,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
MAX_MSG = 4000

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PREMIUM PERSISTENCE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PREMIUM_FILE = os.environ.get("PREMIUM_FILE", "premium_users.json")

def _save_premium_file(bot_data: dict) -> None:
    now = time.time()
    all_users = bot_data.get("user_data", {})
    premium = {}
    for uid_str, ud in all_users.items():
        plan = ud.get("plan", "TRIAL").upper()
        expires = ud.get("expires", 0)
        if plan != "TRIAL" and expires > now:
            premium[uid_str] = {
                "plan": plan,
                "expires": expires,
                "name": ud.get("name", ""),
                "username": ud.get("username", ""),
                "last_receipt": ud.get("last_receipt", ""),
            }
    try:
        with open(PREMIUM_FILE, "w", encoding="utf-8") as f:
            json.dump(premium, f, indent=2)
        logger.info(f"[PREMIUM] JSON backup: {len(premium)} user(s) → {PREMIUM_FILE}")
    except Exception as exc:
        logger.warning(f"[PREMIUM] JSON save failed: {exc}")

async def _save_premium(bot_data: dict) -> None:
    await asyncio.to_thread(_save_premium_file, bot_data)
    await db.save_all_now(bot_data.get("user_data", {}))

def _load_premium_file(bot_data: dict) -> None:
    if not os.path.exists(PREMIUM_FILE):
        logger.info(f"[PREMIUM] {PREMIUM_FILE} not found — starting fresh.")
        return
    try:
        with open(PREMIUM_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except Exception as exc:
        logger.warning(f"[PREMIUM] Load failed: {exc}")
        return

    now = time.time()
    user_data = bot_data.setdefault("user_data", {})
    restored = 0
    for uid_str, pdata in saved.items():
        expires = pdata.get("expires", 0)
        if expires <= now:
            continue
        plan = pdata.get("plan", "TRIAL").upper()
        if plan == "TRIAL":
            continue
        ud = user_data.setdefault(uid_str, {})
        ud["plan"] = plan
        ud["expires"] = expires
        if pdata.get("name"):
            ud.setdefault("name", pdata["name"])
        if pdata.get("username"):
            ud.setdefault("username", pdata["username"])
        if pdata.get("last_receipt"):
            ud.setdefault("last_receipt", pdata["last_receipt"])
        restored += 1

    logger.info(f"[PREMIUM] Restored {restored} premium user(s) from {PREMIUM_FILE}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FORCE-JOIN LIST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FORCE_JOIN_LIST = [
    ("Batcardchk", "https://t.me/Batcardchk", "📢 Main Channel"),
    ("batcardchkGroup", "https://t.me/batcardchkGroup", "👥 Main Group"),
]

_config_fc = [(u, l) for u, l in FORCE_CHANNELS]
for _fc_entry in FORCE_JOIN_LIST:
    _uname = _fc_entry[0]
    if not any(_uname == u for u, _ in _config_fc):
        _config_fc.append((_uname, _fc_entry[1]))

FORCE_JOIN_FULL: list[tuple[str, str, str]] = []
_label_map = {e[0]: e[2] for e in FORCE_JOIN_LIST}
for _uname, _link in _config_fc:
    _label = _label_map.get(_uname, f"📢 @{_uname}")
    FORCE_JOIN_FULL.append((_uname, _link, _label))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INSTANCE LOCK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_lock_file_handle = None

def _stale_lock() -> bool:
    try:
        with open(LOCK_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return False
    except (FileNotFoundError, ValueError):
        return True
    except ProcessLookupError:
        return True
    except PermissionError:
        return False

def acquire_instance_lock() -> bool:
    global _lock_file_handle
    if _stale_lock():
        try:
            os.unlink(LOCK_FILE)
        except FileNotFoundError:
            pass
    try:
        _lock_file_handle = open(LOCK_FILE, "w")
        fcntl.flock(_lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file_handle.write(str(os.getpid()))
        _lock_file_handle.flush()
        return True
    except (IOError, OSError):
        return False

def release_instance_lock():
    global _lock_file_handle
    if _lock_file_handle:
        try:
            fcntl.flock(_lock_file_handle, fcntl.LOCK_UN)
            _lock_file_handle.close()
            os.unlink(LOCK_FILE)
        except Exception:
            pass
        _lock_file_handle = None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BOLD UNICODE FONT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def B(text: str) -> str:
    bold_map = {
        'A': '𝗔', 'B': '𝗕', 'C': '𝗖', 'D': '𝗗', 'E': '𝗘', 'F': '𝗙', 'G': '𝗚', 'H': '𝗛',
        'I': '𝗜', 'J': '𝗝', 'K': '𝗞', 'L': '𝗟', 'M': '𝗠', 'N': '𝗡', 'O': '𝗢', 'P': '𝗣',
        'Q': '𝗤', 'R': '𝗥', 'S': '𝗦', 'T': '𝗧', 'U': '𝗨', 'V': '𝗩', 'W': '𝗪', 'X': '𝗫',
        'Y': '𝗬', 'Z': '𝗭', 'a': '𝗮', 'b': '𝗯', 'c': '𝗰', 'd': '𝗱', 'e': '𝗲', 'f': '𝗳',
        'g': '𝗴', 'h': '𝗵', 'i': '𝗶', 'j': '𝗷', 'k': '𝗸', 'l': '𝗹', 'm': '𝗺', 'n': '𝗻',
        'o': '𝗼', 'p': '𝗽', 'q': '𝗾', 'r': '𝗿', 's': '𝘀', 't': '𝘁', 'u': '𝘂', 'v': '𝘃',
        'w': '𝘄', 'x': '𝘅', 'y': '𝘆', 'z': '𝘇', '0': '𝟬', '1': '𝟭', '2': '𝟮', '3': '𝟯',
        '4': '𝟰', '5': '𝟱', '6': '𝟲', '7': '𝟳', '8': '𝟴', '9': '𝟵',
    }
    return "".join(bold_map.get(ch, ch) for ch in text)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RAW MARKUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class RawMarkup(TelegramObject):
    __slots__ = ("_data",)

    def __init__(self, inline_keyboard: list):
        super().__init__()
        self._data = {"inline_keyboard": inline_keyboard}

    def to_dict(self, api_kwargs=None) -> dict:
        return self._data

    def to_json(self) -> str:
        return json.dumps(self._data)

def _btn(text: str, *, cb: str = None, url: str = None,
         style: str = None, icon: str = None) -> dict:
    d: dict = {"text": text}
    if cb:   d["callback_data"] = cb
    if url:  d["url"] = url
    if style: d["style"] = style
    if icon:  d["icon_custom_emoji_id"] = icon
    return d

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MODERN UI KEYBOARDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def kb_main_modern(user_id: int) -> RawMarkup:
    """Modern main menu with gradient-style buttons"""
    return RawMarkup([
        [
            _btn(f"{_te(GLOW_FIRE, '🔥')} Checker", cb="mgates", style="primary"),
            _btn(f"{_te(GLOW_DIAMOND, '💎')} Buy Now", cb="mprice", style="primary"),
        ],
        [
            _btn(f"{_te(GLOW_STAR, '✨')} Updates", url=CHANNEL_LINK, style="primary"),
            _btn(f"{_te(GLOW_CROWN, '👑')} Referral", cb="mreferral", style="primary"),
        ],
        [
            _btn(f"{_te(MODERN_USER_EMOJI, '👤')} Profile", cb="mprofile", style="primary"),
            _btn(f"🆘 Support", url=SUPPORT_LINK, style="primary"),
        ],
    ])

def kb_price_modern() -> RawMarkup:
    """Modern price menu with premium styling"""
    return RawMarkup([
        [
            _btn(f"{_te(GLOW_STAR, '⭐')} 1.5$ — 1 Day", cb="pay1d", style="primary"),
            _btn(f"{_te(GLOW_STAR, '⭐')} 8$ — 7 Days", cb="pay10", style="primary"),
        ],
        [
            _btn(f"{_te(GLOW_STAR, '⭐')} 12$ — 15 Days", cb="pay15", style="primary"),
            _btn(f"{_te(GLOW_STAR, '⭐')} 25$ — 30 Days", cb="pay30", style="primary"),
        ],
        [
            _btn(f"🆘 Support", url=SUPPORT_LINK, style="primary"),
        ],
        [
            _btn(f"🔙 Back", cb="bmain"),
        ],
    ])

def kb_gate_main_modern() -> RawMarkup:
    """Modern gate selection menu"""
    return RawMarkup([
        [
            _btn(f"{_te(GLOW_FIRE, '🔥')} Shopify Mass", cb="imsh", style="primary"),
            _btn(f"{_te(GLOW_SPARKLE, '✦')} Shopify Single", cb="ish", style="primary"),
        ],
        [
            _btn(f"🔙 Back", cb="bmain"),
        ],
    ])

def kb_back_modern(cb: str, label: str = "Back") -> RawMarkup:
    """Modern back button"""
    return RawMarkup([
        [_btn(f"🔙 {label}", cb=cb, style="primary")],
    ])

def kb_upgrade_modern() -> RawMarkup:
    """Modern upgrade prompt"""
    return RawMarkup([
        [_btn(f"{_te(GLOW_DIAMOND, '💎')} Buy Premium", cb="mprice", style="primary")],
        [_btn(f"🆘 Support", url=SUPPORT_LINK, style="primary")],
    ])

def kb_result_modern(is_premium: bool = False) -> RawMarkup:
    """Modern result card buttons"""
    if is_premium:
        return RawMarkup([
            [
                _btn(f"🤖 Open Bot", url=BOT_LINK, style="primary"),
                _btn(f"📢 Channel", url=CHANNEL_LINK, style="primary"),
            ],
        ])
    return RawMarkup([
        [
            _btn(f"{_te(GLOW_DIAMOND, '💎')} Buy Premium — Unlimited", cb="mprice", style="primary"),
        ],
        [
            _btn(f"📢 @Batcardchk", url=CHANNEL_LINK, style="primary"),
        ],
    ])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _te(eid: str, fb: str = "●") -> str:
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'

def get_styled_plan(raw_plan: str) -> str:
    p = raw_plan.upper()
    if p == "CORE":  return B("Core")
    if p == "ELITE": return B("Elite")
    if p == "ROOT":  return B("Root")
    return B("Trial")

def get_plan_icon(raw_plan: str) -> str:
    return "👑" if raw_plan.upper() in ("CORE", "ELITE", "ROOT") else ""

def get_user_data(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> dict:
    uid = str(user_id)
    if "user_data" not in context.bot_data:
        context.bot_data["user_data"] = {}
    if uid not in context.bot_data["user_data"]:
        context.bot_data["user_data"][uid] = {
            "name": "User", "first_name": "User", "last_name": "", "username": "",
            "language_code": "en", "joined": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "last_active": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "credits": 150, "plan": "TRIAL", "expires": 0, "pre_premium_credits": 0,
            "total_refs": 0, "total_checks": 0, "approved_checks": 0, "declined_checks": 0,
            "last_gate": "N/A", "last_card": "N/A", "codes_redeemed": 0, "keys_redeemed": 0,
            "banned": False, "total_charged": 0,
        }
    return context.bot_data["user_data"][uid]

def _update_user_meta(ud: dict, user) -> None:
    ud["first_name"] = user.first_name or "User"
    ud["last_name"] = user.last_name or ""
    ud["name"] = user.full_name or user.first_name or "User"
    ud["last_active"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if user.username: ud["username"] = user.username
    if getattr(user, "language_code", None): ud["language_code"] = user.language_code

def is_user_premium(ud: dict) -> bool:
    raw_plan = ud.get("plan", "TRIAL").upper()
    is_prem = raw_plan != "TRIAL"
    if is_prem and ud.get("expires", 0) <= time.time():
        saved = ud.get("pre_premium_credits", 0)
        ud["plan"] = "TRIAL"
        ud["credits"] = max(saved, 0)
        ud["expires"] = 0
        ud["pre_premium_credits"] = 0
        return False
    return is_prem

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COOLDOWN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SINGLE_CHECK_COOLDOWN = 25

def get_cooldown_remaining(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> float:
    store = context.bot_data.setdefault("cooldown_store", {})
    last = store.get(user_id, 0)
    remaining = SINGLE_CHECK_COOLDOWN - (time.time() - last)
    return max(0.0, remaining)

def set_cooldown(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    context.bot_data.setdefault("cooldown_store", {})[user_id] = time.time()

def gen_code(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

def gen_receipt() -> str:
    return f"Batamanchk{random.randint(100000, 999999)}-CHK"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECURE REFERRAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_REF_SECRET: bytes = BOT_TOKEN.encode("utf-8")

def _ref_token(user_id: int) -> str:
    msg = str(user_id).encode("utf-8")
    sig = hmac.new(_REF_SECRET, msg, hashlib.sha256).hexdigest()[:16]
    return f"{user_id}_{sig}"

def _verify_ref_token(token: str):
    try:
        uid_str, sig = token.rsplit("_", 1)
        uid = int(uid_str)
        expected = hmac.new(_REF_SECRET, str(uid).encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(sig, expected):
            return uid
    except Exception:
        pass
    return None

def get_referral_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{_ref_token(user_id)}"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UI — MODERN USER CONTROL HUB
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ui_profile_modern(user, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Modern profile with better styling"""
    ud = get_user_data(user.id, context)
    raw_plan = ud.get("plan", "TRIAL").upper()
    expires = ud.get("expires", 0)
    now = time.time()
    if raw_plan != "TRIAL" and expires <= now:
        raw_plan = "TRIAL"
        ud["plan"] = "TRIAL"
        ud["expires"] = 0
        expires = 0
    premium = raw_plan != "TRIAL"
    credits = "Unlimited" if premium else str(ud.get("credits", 150))
    plan_emoji = _te(get_plan_emoji_id(raw_plan), "⭐")
    uname = escape(f"@{user.username}" if user.username else user.first_name or "User")
    joined = ud.get("joined", datetime.now().strftime("%Y-%m-%d")).split(" ")[0]
    last_active = ud.get("last_active", "N/A")
    total_refs = ud.get("total_refs", 0)
    total_checks = ud.get("total_checks", 0)
    ban_status = f"{_te(MODERN_ERRORS, '⚠️')} {B('Banned')}" if ud.get("banned", False) else f"{_te(MODERN_LIVE, '✅')} {B('Active')}"

    if premium and expires > now:
        exp_date = datetime.fromtimestamp(expires).strftime("%Y-%m-%d")
        rem_d = int((expires - now) / 86400)
        rem_h = int(((expires - now) % 86400) / 3600)
        expire_line = f"✰ {_te(GLOW_STAR, '✨')} <b>Expires</b> ➔ {exp_date} ({rem_d}d {rem_h}h)"
    else:
        expire_line = f"✰ {_te(GLOW_STAR, '✨')} <b>Expires</b> ➔ Never (Trial)"

    return (
        f"{_te(GLOW_CROWN, '👑')} <b>User Control Hub</b> {_te(GLOW_CROWN, '👑')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✰ {_te(MODERN_USER_EMOJI, '👤')} <b>Username</b> ➔ {uname} {plan_emoji}\n"
        f"✰ <b>User ID</b> ➔ <code>{user.id}</code>\n"
        f"✰ {_te(GLOW_SPARKLE, '✦')} <b>Access</b> ➔ {get_styled_plan(raw_plan)}\n"
        f"✰ {_te(MODERN_LIVE, '✅')} <b>Status</b> ➔ {ban_status}\n"
        f"✰ {_te(GLOW_DIAMOND, '💎')} <b>Credits</b> ➔ {credits}\n"
        f"✰ {_te(MODERN_TIME_EMOJI, '⏱')} <b>Joined</b> ➔ {joined}\n"
        f"{expire_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✰ {_te(MODERN_TIME_EMOJI, '⏱')} <b>Last Active</b> ➔ {last_active}\n"
        f"✰ {_te(MODERN_CARD_EMOJI, '💳')} <b>Total Checks</b> ➔ {total_checks}\n"
        f"✰ {_te(GLOW_CROWN, '👑')} <b>Referrals</b> ➔ {total_refs} (+{total_refs * REFERRAL_CREDITS} credits)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{_te(MODERN_DEV_EMOJI, '⚡')} Version ➔ {VERSION} | <a href='{DEV_LINK}'>Batamanchk</a> {_te(MODERN_PRO_EMOJI, '⭐')}"
    )

def ui_start_screen_modern(user, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Modern welcome screen"""
    ud = get_user_data(user.id, context)
    raw_plan = ud.get("plan", "TRIAL").upper()
    expires = ud.get("expires", 0)
    now = time.time()
    if raw_plan != "TRIAL" and expires <= now:
        raw_plan = "TRIAL"
        ud["plan"] = "TRIAL"
        ud["expires"] = 0
    premium = raw_plan != "TRIAL"
    credits = "∞" if premium else str(ud.get("credits", 150))
    uname = escape(user.first_name or "User")
    joined = ud.get("joined", datetime.now().strftime("%Y-%m-%d")).split(" ")[0]
    access = get_styled_plan(raw_plan)

    return (
        f"{_te(GLOW_FIRE, '🔥')} <b>Welcome to Batmancardchk Bot</b> {_te(GLOW_FIRE, '🔥')}\n"
        f"{_te(GLOW_SPARKLE, '✦')}────────────{_te(GLOW_SPARKLE, '✦')}\n"
        f"{_te(MODERN_USER_EMOJI, '👤')} <b>User</b> ➔ {uname}\n"
        f"<b>User ID</b> ➔ <code>{user.id}</code>\n"
        f"{_te(GLOW_SPARKLE, '✦')} <b>Access</b> ➔ {access}\n"
        f"{_te(GLOW_DIAMOND, '💎')} <b>Credits</b> ➔ {credits}\n"
        f"{_te(MODERN_TIME_EMOJI, '⏱')} <b>Joined</b> ➔ {joined}\n"
        f"{_te(GLOW_SPARKLE, '✦')}────────────{_te(GLOW_SPARKLE, '✦')}\n"
        f"Choose an option below.\n"
        f"{_te(GLOW_SPARKLE, '✦')}────────────{_te(GLOW_SPARKLE, '✦')}\n"
        f"{_te(MODERN_DEV_EMOJI, '⚡')} <b>Dev</b> ➔ <a href='{DEV_LINK}'>Batmancardchk</a> {_te(MODERN_PRO_EMOJI, '⭐')}\n"
        f"<b>Version</b> ➔ {VERSION}"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FORCE-SUB CACHE & HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_force_sub_cache: dict = {}
_FS_PASS_TTL = 300
_FS_FAIL_TTL = 30

async def check_force_sub(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list:
    if user_id == OWNER_ID:
        return []

    cached = _force_sub_cache.get(user_id)
    if cached:
        passed, ts, cached_list = cached
        ttl = _FS_PASS_TTL if passed else _FS_FAIL_TTL
        if time.time() - ts < ttl:
            return cached_list

    not_joined = []
    for uname, link, label in FORCE_JOIN_FULL:
        try:
            member = await context.bot.get_chat_member(f"@{uname}", user_id)
            if member.status in ("left", "kicked", "restricted"):
                not_joined.append((uname, link, label))
        except Forbidden:
            logger.warning(f"[FORCE-SUB] Bot has no admin rights in @{uname}.")
            not_joined.append((uname, link, label))
        except BadRequest as e:
            err = str(e).lower()
            if any(x in err for x in (
                "user not found", "user_not_participant",
                "participant_id_invalid", "chat not found",
                "not a member", "not found",
            )):
                not_joined.append((uname, link, label))
        except Exception as exc:
            logger.debug(f"[FORCE-SUB] check error for @{uname}: {exc}")
            pass

    if not not_joined:
        _force_sub_cache[user_id] = (True, time.time(), [])
    else:
        _force_sub_cache[user_id] = (False, time.time(), not_joined)
    return not_joined

def _force_join_text_modern(not_joined: list) -> str:
    total = len(FORCE_JOIN_FULL)
    joined = total - len(not_joined)
    return (
        f"{_te(GLOW_CROWN, '👑')} <b>Join Required</b> {_te(GLOW_CROWN, '👑')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"To use this bot you must join <b>all</b> our\n"
        f"channels and groups listed below.\n"
        f"\n"
        f"{_te(GLOW_STAR, '✨')} <b>Progress:</b> {joined}/{total} joined\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(f"  ✗ {label} <code>@{uname}</code>" for uname, _link, label in not_joined) +
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 Click each button below to join,\n"
        f"then press {_te(MODERN_LIVE, '✅')} <b>Verify</b>."
    )

def kb_force_sub_modern(not_joined: list) -> RawMarkup:
    rows = []
    for uname, link, label in not_joined:
        rows.append([_btn(f"{label} ➔ @{uname}", url=link, style="primary")])
    rows.append([_btn(f"{_te(MODERN_LIVE, '✅')} I Joined All — Verify Now", cb="check_sub", style="primary")])
    return RawMarkup(rows)

async def require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    not_joined = await check_force_sub(update.effective_user.id, context)
    if not_joined:
        await update.message.reply_text(
            _force_join_text_modern(not_joined),
            parse_mode="HTML",
            reply_markup=kb_force_sub_modern(not_joined)
        )
        return False
    return True

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BAN CHECK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def require_not_banned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id == OWNER_ID:
        return True
    ud = get_user_data(user_id, context)
    if ud.get("banned", False):
        try:
            await update.message.reply_text(
                f"{_te(MODERN_ERRORS, '⚠️')} <b>Banned</b>\n"
                f"──────────\n"
                f"You have been banned from using this bot.\n"
                f"Contact support if you think this is a mistake.\n"
                f"──────────",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return False
    return True

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CARD CHECK RESULT — MODERN UI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_check_result_modern(c
