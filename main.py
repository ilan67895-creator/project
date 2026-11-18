"""
================================================================================
שרת האתר - TradePro Web Application (Flask Gateway)
פרויקט שוק ההון (5 יחידות לימוד במדעי המחשב)
================================================================================
קובץ זה מנהל את שרת ה-Web של המערכת:
1. מציג את דפי ה-HTML (תבניות Jinja2 מעוצבות).
2. מתקשר עם שרת הלוגיקה דרך TCP Sockets בפורט 9999.
3. דוגם נתוני מניות בזמן אמת מ-Yahoo Finance בריבוי נימים.
4. מנהל את שלבי ההזדהות (Sessions) והרשאות מנהל (Admin).
"""

import json
import os
import secrets
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

# ==============================================================================
# הגדרות שרת וקבועים (Configuration & Constants)
# ==============================================================================
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# הגדרת כתובת ופורט שרת הלוגיקה
LOGIC_SERVER_HOST = "127.0.0.1"
LOGIC_SERVER_PORT = 9999

# רשימת המניות המוצגות בלוח המעקב
STOCKS = {
    "AAPL": "Apple",
    "NVDA": "NVIDIA",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "TSLA": "Tesla",
}


class QuoteProviderError(Exception):
    """שגיאה מותאמת אישית לטיפול בכשלים בעת קבלת נתוני מניות."""
    pass


# ==============================================================================
# 1. פונקציית תקשורת סוקטים מול שרת הלוגיקה (Socket Communication Helper)
# ==============================================================================
def send_to_logic_server(message: str) -> str:
    """
    פותח ערוץ שיחה TCP מול שרת הלוגיקה (פורט 9999), מעביר את ההודעה
    ומחזיר את תשובת השרת בצורה מאובטחת.
    """
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(5.0)  # מניעת קפיאה אם השרת לא עונה תוך 5 שניות
        client_socket.connect((LOGIC_SERVER_HOST, LOGIC_SERVER_PORT))

        # שליחת ההודעה מקודדת ב-UTF-8
        client_socket.sendall(message.encode("utf-8"))

        # קבלת התשובה (חוצץ מוגדל עד 64KB כדי לתמוך ברשימות משתמשים גדולות ב-Admin)
        response_bytes = b""
        while True:
            chunk = client_socket.recv(4096)
            if not chunk:
                break
            response_bytes += chunk
            # אם ההודעה אינה דורשת המשך קריאה
            if len(chunk) < 4096:
                break

        client_socket.close()
        return response_bytes.decode("utf-8")
    except socket.timeout:
        return "Error: Connection timed out to Logic Server."
    except Exception:
        # טיפול במקרה ששרת הלוגיקה אינו פועל
        return "Error: The Logic Socket Server is not running!"


# ==============================================================================
# 2. קבלת נתוני מניות בזמן אמת (Stock Market Live Quotes - Yahoo Finance)
# ==============================================================================
def fetch_stock_quote(symbol: str, company: str) -> dict:
    """
    דוגם נתוני מסחר עדכניים עבור מניה ספציפית באמצעות API ציבורי של Yahoo Finance.
    מחשב את שינוי המחיר בדולר ובאחוזים ומחזיר מילון נתונים מעוצב.
    """
    query = urlencode({"range": "1d", "interval": "1m"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
    req = Request(url, headers={"User-Agent": "TradePro/1.0"})

    try:
        with urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise QuoteProviderError(f"Could not load {symbol} quote") from error

    try:
        result = payload["chart"]["result"][0]
        metadata = result["meta"]
        price = float(metadata["regularMarketPrice"])
        previous_close = float(
            metadata.get("chartPreviousClose") or metadata["previousClose"]
        )
        timestamp = int(metadata["regularMarketTime"])
        currency = metadata.get("currency", "USD")
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise QuoteProviderError(f"Invalid quote data for {symbol}") from error

    if previous_close == 0:
        raise QuoteProviderError(f"Invalid previous close for {symbol}")

    change = price - previous_close
    change_percent = (change / previous_close) * 100

    return {
        "symbol": symbol,
        "company": company,
        "price": price,
        "change": change,
        "change_percent": change_percent,
        "currency": currency,
        "updated_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
    }


# ==============================================================================
# 3. נתיבי הזדהות ומשתמשים (Authentication Routes)
# ==============================================================================
@app.route("/")
def index():
    """דף הבית / כניסה למערכת. אם המשתמש כבר מחובר, מועבר ישירות ללוח המעקב."""
    if "username" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/login", methods=["POST"])
def login():
    """מקבל פרטי התחברות, שולח לאימות בשרת הלוגיקה ושומר את הסשן."""
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    response = send_to_logic_server(f"LOGIN|{username}|{password}")

    if response.startswith("SUCCESS"):
        parts = response.split("|")
        fullname = parts[1] if len(parts) > 1 else username
        is_admin = (parts[2] == "1") if len(parts) > 2 else False

        # שמירת נתוני המשתמש בסשן המאובטח של הדפדפן
        session.clear()
        session["username"] = username
        session["fullname"] = fullname
        session["is_admin"] = is_admin
        return redirect(url_for("dashboard"))
    else:
        error_msg = response if response.startswith("Error:") else "שם משתמש או סיסמה שגויים, אנא נסה שנית."
        return render_template("index.html", error=error_msg)


@app.route("/register", methods=["GET", "POST"])
def register():
    """רישום חשבון חדש במערכת והעברה לדף ההתחברות בסיום מוצלח."""
    if request.method == "POST":
        fullname = request.form.get("fullname", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        response = send_to_logic_server(f"REGISTER|{fullname}|{username}|{password}")

        if "SUCCESS" in response:
            return render_template(
                "index.html",
                success="ההרשמה בוצעה בהצלחה! כעת תוכל להתחבר לחשבונך החדש."
            )
        else:
            return render_template("register.html", error=response)

    return render_template("register.html")


@app.route("/logout", methods=["POST"])
def logout():
    """מנתק את המשתמש הנוכחי על ידי ניקוי נתוני ה-Session ומחזיר לדף הבית."""
    session.clear()
    return redirect(url_for("index"))


# ==============================================================================
# 4. נתיבי שחזור ואיפוס סיסמה (Password Recovery Routes)
# ==============================================================================
@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    """שלב 1: הזנת כתובת דוא\"ל לצורך קבלת קוד אימות חד-פעמי (OTP)."""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        response = send_to_logic_server(f"REQUEST_CODE|{email}")

        if "SUCCESS" in response:
            return redirect(url_for("reset_password", email=email))
        else:
            return render_template("forgot_password.html", error=response)

    return render_template("forgot_password.html")


@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    """שלב 2: הזנת קוד האימות שהתקבל במייל והגדרת סיסמה חדשה."""
    email = request.args.get("email", "")

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        code = request.form.get("code", "").strip()
        new_password = request.form.get("new_password", "").strip()

        response = send_to_logic_server(f"RESET_WITH_CODE|{email}|{code}|{new_password}")

        if "SUCCESS" in response:
            return render_template(
                "index.html",
                success="הסיסמה אופסה בהצלחה! כעת תוכל להתחבר עם הסיסמה החדשה."
            )
        else:
            return render_template("reset_password.html", email=email, error=response)

    return render_template("reset_password.html", email=email)


# ==============================================================================
# 5. לוח מעקב ומסחר במניות (Dashboard & Market Data API)
# ==============================================================================
@app.route("/dashboard")
def dashboard():
    """מציג את לוח המעקב (Watchlist), היתרה הכספית ותיק ההשקעות רק למשתמש מחובר."""
    if "username" not in session:
        return redirect(url_for("index"))

    balance = 10000.00
    portfolio = []
    trades_history = []

    user_resp = send_to_logic_server(f"GET_USER_DATA|{session['username']}")
    if user_resp.startswith("SUCCESS|"):
        try:
            data = json.loads(user_resp.split("|", 1)[1])
            balance = float(data.get("balance", 10000.00))
            portfolio = data.get("portfolio", [])
            trades_history = data.get("trades", [])
        except (json.JSONDecodeError, ValueError):
            pass

    return render_template(
        "dashboard.html",
        current_user=session["fullname"],
        balance=balance,
        portfolio=portfolio,
        trades_history=trades_history,
        stocks=STOCKS,
    )


@app.route("/api/user_data")
def api_user_data():
    """מחזיר יתרה, תיק מניות והיסטוריית עסקאות של המשתמש המחובר בפורמט JSON."""
    if "username" not in session:
        return jsonify({"error": "Please sign in."}), 401

    user_resp = send_to_logic_server(f"GET_USER_DATA|{session['username']}")
    if user_resp.startswith("SUCCESS|"):
        try:
            data = json.loads(user_resp.split("|", 1)[1])
            return jsonify(data)
        except json.JSONDecodeError:
            return jsonify({"error": "Failed to parse user data."}), 500

    return jsonify({"error": "Could not retrieve user data."}), 500


@app.route("/api/trade", methods=["POST"])
def trade():
    """
    ביצוע פקודת מסחר (BUY או SELL) בזמן אמת לפי מחיר השוק העדכני.
    """
    if "username" not in session:
        return jsonify({"error": "Please sign in to trade."}), 401

    data = request.get_json(silent=True) or {}
    symbol = str(data.get("symbol", "")).strip().upper()
    action = str(data.get("action", "")).strip().upper()

    try:
        shares = int(data.get("shares", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid shares quantity"}), 400

    if symbol not in STOCKS:
        return jsonify({"error": f"Invalid stock symbol '{symbol}'"}), 400

    if action not in ("BUY", "SELL"):
        return jsonify({"error": "Action must be BUY or SELL"}), 400

    if shares <= 0:
        return jsonify({"error": "Shares quantity must be at least 1"}), 400

    # קבלת מחיר המניה בזמן אמת
    try:
        quote = fetch_stock_quote(symbol, STOCKS[symbol])
        current_price = float(quote["price"])
    except Exception as err:
        return jsonify({"error": f"Could not retrieve real-time price for {symbol}: {err}"}), 502

    response = send_to_logic_server(
        f"EXECUTE_TRADE|{session['username']}|{symbol}|{action}|{shares}|{current_price}"
    )

    if response.startswith("SUCCESS"):
        parts = response.split("|")
        message = parts[1] if len(parts) > 1 else "Trade executed successfully"
        payload = json.loads(parts[2]) if len(parts) > 2 else {}
        return jsonify({
            "success": True,
            "message": message,
            "symbol": symbol,
            "action": action,
            "shares": shares,
            "price": current_price,
            "total": round(shares * current_price, 2),
            "new_balance": payload.get("new_balance"),
            "portfolio": payload.get("portfolio", []),
        })
    else:
        err = response.split("|", 1)[1] if "|" in response else response
        if err.startswith("FAIL: "):
            err = err[6:]
        return jsonify({"error": err}), 400


@app.route("/api/quotes")
def quotes():
    """
    נקודת קצה (API) המחזירה את מחירי המניות בפורמט JSON עבור הסקריפט בדפדפן.
    מבצעת דגימה מקבילית בעזרת ThreadPoolExecutor לשיפור ביצועים.
    """
    if "username" not in session:
        return jsonify({"error": "Please sign in to view stock quotes."}), 401

    with ThreadPoolExecutor(max_workers=len(STOCKS)) as executor:
        futures = {
            symbol: executor.submit(fetch_stock_quote, symbol, company)
            for symbol, company in STOCKS.items()
        }
        try:
            quotes_data = [future.result() for future in futures.values()]
        except QuoteProviderError as error:
            return jsonify({"error": str(error)}), 502

    return jsonify({"quotes": quotes_data})


# ==============================================================================
# 6. לוח מנהל המערכת (Admin Dashboard)
# ==============================================================================
@app.route("/admin")
def admin_dashboard():
    """
    לוח בקרה למנהל המערכת: מאובטח ומאפשר כניסה אך ורק למשתמשים בעלי הרשאת מנהל.
    """
    if "username" not in session or not session.get("is_admin"):
        # חסימת גישה למי שאינו מנהל
        return redirect(url_for("index"))

    raw_response = send_to_logic_server("GET_ADMIN_DATA")

    if raw_response.startswith("SUCCESS"):
        json_str = raw_response.split("|", 1)[1]
        try:
            data = json.loads(json_str)
            success_msg = request.args.get("success")
            error_msg = request.args.get("error")
            return render_template(
                "admin.html",
                users=data["users"],
                logs=data["logs"],
                success=success_msg,
                error=error_msg,
            )
        except json.JSONDecodeError:
            return "<h1>שגיאה בפענוח נתוני מנהל</h1>", 500
    else:
        return f"<h1>שגיאה בטעינת נתוני מנהל: {raw_response}</h1>", 500


@app.route("/admin/delete_user/<int:user_id>", methods=["POST"])
def admin_delete_user(user_id):
    """
    מחיקת משתמש על ידי מנהל מערכת בלבד.
    """
    if "username" not in session or not session.get("is_admin"):
        return redirect(url_for("index"))

    admin_username = session.get("username", "")
    response = send_to_logic_server(f"DELETE_USER|{user_id}|{admin_username}")

    if response.startswith("SUCCESS"):
        return redirect(url_for("admin_dashboard", success="המשתמש נמחק בהצלחה מהמערכת."))
    else:
        error_msg = response.split("|", 1)[1] if "|" in response else response
        if error_msg.startswith("FAIL: "):
            error_msg = error_msg[6:]
        return redirect(url_for("admin_dashboard", error=error_msg))


# ==============================================================================
# הפעלת השרת
# ==============================================================================
if __name__ == "__main__":
    app.run(debug=True, port=5000)
