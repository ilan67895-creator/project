"""
================================================================================
שרת לוגיקה ובסיס נתונים - Logic Server & Database
פרויקט TradePro / שוק ההון (5 יחידות לימוד במדעי המחשב)
================================================================================
שרת זה אחראי על:
1. ניהול החיבור מול בסיס הנתונים MySQL (XAMPP).
2. אימות משתמשים והרשמות חדשות עם הצפנת סיסמאות מאובטחת (Werkzeug).
3. איפוס סיסמא באמצעות קוד חד-פעמי (OTP) הנשלח בדוא"ל (SMTP).
4. מתן נתוני ניהול (Admin Console) ברישום יומני התחברות (Audit Logs).
5. טיפול בריבוי בקשות במקביל בעזרת ריבוי נימים (Multi-Threading).
"""

import json
import os
import random
import smtplib
import socket
import threading
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from typing import Any, Optional, cast

import mysql.connector
from werkzeug.security import check_password_hash, generate_password_hash

# ==============================================================================
# הגדרות שרת ובסיס נתונים (Configuration & Constants)
# ==============================================================================
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9999

DB_HOST = "localhost"
DB_USER = "root"
DB_PASSWORD = ""
DB_NAME = "tradepro_db"

# הגדרות שליחת מיילים דרך Gmail SMTP
# -----------------------------------------------------------------------------
SMTP_SENDER_EMAIL = os.environ.get("TRADEPRO_EMAIL", "ilan67895@gmail.com")
SMTP_SENDER_PASSWORD = os.environ.get("TRADEPRO_EMAIL_PASSWORD", "cnxj tlzg pcck mhjo")


# ==============================================================================
# 1. ניהול בסיס הנתונים (Database Connection & Initialization)
# ==============================================================================
def get_db_connection() -> Any:
    """יוצר ומחזיר אובייקט חיבור לבסיס הנתונים MySQL של XAMPP."""
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
    )


def initialize_database() -> None:
    """
    יוצר באופן אוטומטי את בסיס הנתונים ואת הטבלאות הנדרשות בעת הפעלת השרת,
    ומוודא שקיימות כל העמודות הנחוצות (כולל הרשאת מנהל - is_admin).
    """
    try:
        # שלב א': התחברות לשרת MySQL הראשי ובדיקה שבסיס הנתונים קיים
        admin_conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        admin_cursor = admin_conn.cursor()
        admin_cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
        admin_conn.commit()
        admin_cursor.close()
        admin_conn.close()

        # שלב ב': התחברות לבסיס הנתונים הספציפי ויצירת הטבלאות
        db = get_db_connection()
        cursor = db.cursor()

        # טבלת משתמשים
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(255) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                fullname VARCHAR(255) NOT NULL,
                balance DECIMAL(12, 2) DEFAULT 10000.00,
                is_admin BOOLEAN DEFAULT FALSE,
                reset_code VARCHAR(10) NULL,
                reset_code_expires DATETIME NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # טבלת יומן התחברויות (Audit / Login Logs)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS login_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                status VARCHAR(20) NOT NULL,
                login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # טבלת תיק השקעות (User Portfolio Holdings)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_portfolio (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                shares INT NOT NULL,
                average_price DECIMAL(12, 2) NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY user_symbol_idx (user_id, symbol)
            )
            """
        )

        # טבלת היסטוריית פעולות מסחר (Trade History)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                action VARCHAR(10) NOT NULL,
                shares INT NOT NULL,
                price DECIMAL(12, 2) NOT NULL,
                total_amount DECIMAL(12, 2) NOT NULL,
                trade_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # וידוא שכל המשתמשים הקיימים בעלי יתרה תקינה (לפחות 10,000 דולר)
        cursor.execute("UPDATE users SET balance = 10000.00 WHERE balance IS NULL")

        # בדיקה והוספת עמודת is_admin במידה והטבלה כבר הייתה קיימת ללא העמודה
        cursor.execute(
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'users' AND COLUMN_NAME = 'is_admin'
            """,
            (DB_NAME,),
        )
        row = cursor.fetchone()
        if not row or row[0] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE")

        # הגדרת המשתמש שלך כמנהל מערכת (Admin) באופן אוטומטי
        if SMTP_SENDER_EMAIL:
            cursor.execute(
                "UPDATE users SET is_admin = TRUE WHERE username = %s",
                (SMTP_SENDER_EMAIL,),
            )

        db.commit()
        cursor.close()
        db.close()
        print("[LOGIC SERVER] Database initialized and verified successfully.")
    except mysql.connector.Error as err:
        print(f"[LOGIC SERVER ERROR] Failed to initialize database: {err}")
        raise


# ==============================================================================
# 2. שירות שליחת אימיילים (Email / OTP Service)
# ==============================================================================
def send_otp_email(target_email: str, code: str) -> None:
    """
    שולח קוד אימות חד-פעמי למייל של המשתמש בעזרת פרוטוקול SMTP של Gmail.
    משתמש ב-SMTP_SENDER_EMAIL ו-SMTP_SENDER_PASSWORD המוגדרים בראש הקובץ.
    """
    sender_email = SMTP_SENDER_EMAIL.strip()
    sender_password = SMTP_SENDER_PASSWORD.replace(" ", "").strip()

    if not sender_email or not sender_password:
        raise RuntimeError("לא הוגדרו כתובת מייל וסיסמת אפליקציה ב-logic_server.py")

    body_text = (
        f"שלום,\n\n"
        f"קוד האימות שלך למערכת TradePro הוא: {code}\n"
        f"הקוד תקף ל-10 דקות בלבד.\n\n"
        f"אם לא ביקשת איפוס סיסמה, אנא התעלם מהודעה זו.\n\n"
        f"בברכה,\nצוות TradePro"
    )

    message = MIMEText(body_text, "plain", "utf-8")
    message["Subject"] = str(Header("קוד לאיפוס סיסמה - TradePro", "utf-8"))
    message["From"] = f"TradePro <{sender_email}>"
    message["To"] = target_email

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, target_email, message.as_string())


# ==============================================================================
# 3. פונקציות לוגיקה עסקית (Business Logic Handlers)
# ==============================================================================
def handle_login(parts: list[str]) -> str:
    """
    מטפל בבקשת התחברות:
    מאמת שם משתמש וסיסמה מוצפנת, מתעד את ניסיון ההתחברות ביומן, ומחזיר הרשאה.
    """
    if len(parts) < 3:
        return "FAIL: Missing username or password"

    username = parts[1]
    password_entered = parts[2]

    db = get_db_connection()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, password, fullname, is_admin FROM users WHERE username = %s",
            (username,),
        )
        user = cast(Optional[dict[str, Any]], cursor.fetchone())

        if user and check_password_hash(str(user.get("password", "")), password_entered):
            # רישום התחברות מוצלחת
            cursor.execute(
                "INSERT INTO login_logs (username, status) VALUES (%s, %s)",
                (username, "SUCCESS"),
            )
            db.commit()
            print(f"[LOGIC SERVER] Login SUCCESS for user: {username}")
            admin_flag = "1" if user.get("is_admin") else "0"
            fullname = str(user.get("fullname", username))
            return f"SUCCESS|{fullname}|{admin_flag}"
        else:
            # רישום התחברות שנכשלה
            cursor.execute(
                "INSERT INTO login_logs (username, status) VALUES (%s, %s)",
                (username, "FAILED"),
            )
            db.commit()
            print(f"[LOGIC SERVER] Login FAILED for user: {username}")
            return "FAIL: Invalid username or password"
    finally:
        db.close()


def handle_register(parts: list[str]) -> str:
    """
    מטפל ברישום משתמש חדש:
    בודק שהמשתמש לא קיים, מצפין את הסיסמה ומוסיף לחשבון יתרה התחלתית של 10,000$.
    """
    if len(parts) < 4:
        return "FAIL: Missing registration parameters"

    fullname = parts[1]
    username = parts[2]
    raw_password = parts[3]

    db = get_db_connection()
    try:
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            return "FAIL: Username already exists"

        hashed_password = generate_password_hash(raw_password)
        cursor.execute(
            """
            INSERT INTO users (username, password, fullname, balance, is_admin)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (username, hashed_password, fullname, 10000.00, False),
        )
        db.commit()
        print(f"[LOGIC SERVER] Registered new user: {username}")
        return "SUCCESS"
    finally:
        db.close()


def handle_request_code(parts: list[str]) -> str:
    """
    מטפל בבקשת קוד לאיפוס סיסמה:
    מייצר קוד בן 6 ספרות ותוקף ל-10 דקות, ושולח אותו לכתובת המייל.
    """
    if len(parts) < 2:
        return "FAIL: Missing email address"

    email = parts[1]

    db = get_db_connection()
    try:
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (email,))
        if not cursor.fetchone():
            return "FAIL: Email address not found"

        otp_code = str(random.randint(100000, 999999))
        expires_at = datetime.now() + timedelta(minutes=10)

        cursor.execute(
            "UPDATE users SET reset_code = %s, reset_code_expires = %s WHERE username = %s",
            (otp_code, expires_at, email),
        )
        db.commit()

        # הדפסת הקוד בצורה בולטת לטרמינל ב-VS Code
        print("\n" + "=" * 60)
        print(f" [OTP CODE] קוד אימות עבור {email}: {otp_code}")
        print(" >> העתק קוד זה והזן אותו במסך הבא של איפוס הסיסמה <<")
        print("=" * 60 + "\n")

        # ניסיון שליחה במייל (אם הוגדר משתמש וסיסמה בגוגל)
        try:
            send_otp_email(email, otp_code)
            print(f"[LOGIC SERVER] Sent password reset OTP email to: {email}")
        except Exception as mail_err:
            print(f"[LOGIC SERVER] שימו לב: המייל לא נשלח ({mail_err}), אך ניתן להמשיך עם הקוד שהודפס למעלה.")

        # מחזירים תמיד SUCCESS כדי לא לתקוע את המשתמש במסך, ולאפשר בדיקות חלקות
        return "SUCCESS"
    finally:
        db.close()


def handle_reset_with_code(parts: list[str]) -> str:
    """
    מטפל בעדכון הסיסמה לאחר הזנת קוד האימות:
    בודק את תוקף הקוד, מצפין את הסיסמה החדשה ומנקה את קוד האימות.
    """
    if len(parts) < 4:
        return "FAIL: Missing reset parameters"

    email = parts[1]
    code_entered = parts[2]
    new_password = parts[3]

    db = get_db_connection()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT reset_code, reset_code_expires FROM users WHERE username = %s",
            (email,),
        )
        user_data = cast(Optional[dict[str, Any]], cursor.fetchone())

        if not user_data or not user_data.get("reset_code"):
            return "FAIL: No reset request found"

        code_in_db = str(user_data.get("reset_code", ""))
        expires_at = user_data.get("reset_code_expires")

        if code_in_db != code_entered:
            return "FAIL: Invalid reset code"

        if isinstance(expires_at, datetime) and datetime.now() > expires_at:
            return "FAIL: Reset code has expired"

        hashed_password = generate_password_hash(new_password)
        cursor.execute(
            """
            UPDATE users 
            SET password = %s, reset_code = NULL, reset_code_expires = NULL 
            WHERE username = %s
            """,
            (hashed_password, email),
        )
        db.commit()
        print(f"[LOGIC SERVER] Password successfully updated for: {email}")
        return "SUCCESS"
    finally:
        db.close()


def handle_get_admin_data() -> str:
    """
    מחזיר נתוני ניהול עבור לוח הבקרה (Admin Dashboard):
    רשימת המשתמשים במערכת ו-20 יומני ההתחברות האחרונים.
    """
    db = get_db_connection()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, username, fullname, balance, is_admin FROM users ORDER BY id ASC")
        users = cursor.fetchall()

        cursor.execute(
            """
            SELECT username, status, DATE_FORMAT(login_time, '%Y-%m-%d %H:%i:%s') as login_time 
            FROM login_logs 
            ORDER BY id DESC 
            LIMIT 20
            """
        )
        logs = cursor.fetchall()

        admin_payload = {"users": users, "logs": logs}
        return "SUCCESS|" + json.dumps(admin_payload, default=str)
    finally:
        db.close()


def handle_delete_user(parts: list[str]) -> str:
    """
    מוחק חשבון משתמש מהמערכת לפי מזהה (ID).
    כולל מחיקת נתוני יומן התחברות המשויכים אליו.
    מבנה הפקודה: DELETE_USER|<user_id>|<requesting_admin_username>
    """
    if len(parts) < 2:
        return "FAIL: Missing user id"

    try:
        user_id = int(parts[1])
    except ValueError:
        return "FAIL: Invalid user id"

    requesting_admin = parts[2] if len(parts) > 2 else ""

    db = get_db_connection()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, username, fullname, is_admin FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            return "FAIL: User not found"

        target_username = user["username"]

        if requesting_admin and requesting_admin == target_username:
            return "FAIL: Cannot delete your own admin account"

        # מחיקת רשומות יומן התחברות של המשתמש
        cursor.execute("DELETE FROM login_logs WHERE username = %s", (target_username,))
        # מחיקת נתוני תיק השקעות והיסטוריית עסקאות
        cursor.execute("DELETE FROM user_portfolio WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM trades WHERE user_id = %s", (user_id,))

        # מחיקת המשתמש
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        db.commit()

        print(f"[LOGIC SERVER] User '{target_username}' (ID: {user_id}) deleted successfully by '{requesting_admin}'.")
        return "SUCCESS|User deleted successfully"
    except mysql.connector.Error as err:
        db.rollback()
        print(f"[LOGIC SERVER DATABASE ERROR] Error deleting user ID {user_id}: {err}")
        return f"FAIL: Database error ({err})"
    finally:
        db.close()


def handle_get_user_data(parts: list[str]) -> str:
    """
    מחזיר את נתוני המשתמש: יתרה (Balance), תיק השקעות נוכחי (Portfolio), והיסטוריית עסקאות.
    מבנה הפקודה: GET_USER_DATA|<username>
    """
    if len(parts) < 2:
        return "FAIL: Missing username"

    username = parts[1].strip()
    db = get_db_connection()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, username, fullname, balance, is_admin FROM users WHERE username = %s",
            (username,),
        )
        user = cursor.fetchone()
        if not user:
            return "FAIL: User not found"

        user_id = user["id"]

        # שליפת תיק המניות שברשות המשתמש (רק מניות שבהן הכמות גדולה מ-0)
        cursor.execute(
            "SELECT symbol, shares, average_price FROM user_portfolio WHERE user_id = %s AND shares > 0 ORDER BY symbol ASC",
            (user_id,),
        )
        portfolio = cursor.fetchall()

        # שליפת 10 פעולות המסחר האחרונות
        cursor.execute(
            """
            SELECT symbol, action, shares, price, total_amount, 
                   DATE_FORMAT(trade_time, '%Y-%m-%d %H:%i:%s') as trade_time
            FROM trades 
            WHERE user_id = %s 
            ORDER BY id DESC 
            LIMIT 10
            """,
            (user_id,),
        )
        trades = cursor.fetchall()

        payload = {
            "id": user["id"],
            "username": user["username"],
            "fullname": user["fullname"],
            "balance": float(user["balance"] or 0),
            "portfolio": [
                {
                    "symbol": item["symbol"],
                    "shares": int(item["shares"]),
                    "average_price": float(item["average_price"]),
                }
                for item in portfolio
            ],
            "trades": [
                {
                    "symbol": t["symbol"],
                    "action": t["action"],
                    "shares": int(t["shares"]),
                    "price": float(t["price"]),
                    "total_amount": float(t["total_amount"]),
                    "trade_time": str(t["trade_time"]),
                }
                for t in trades
            ],
        }
        return "SUCCESS|" + json.dumps(payload, default=str)
    except mysql.connector.Error as err:
        print(f"[LOGIC SERVER DATABASE ERROR] Error in handle_get_user_data: {err}")
        return f"FAIL: Database error ({err})"
    finally:
        db.close()


def handle_execute_trade(parts: list[str]) -> str:
    """
    מבצע קנייה או מכירה של מניות ומעדכן את יתרת המשתמש ואת תיק ההשקעות באופן אטומי.
    מבנה הפקודה: EXECUTE_TRADE|<username>|<symbol>|<action>|<shares>|<price>
    """
    if len(parts) < 6:
        return "FAIL: Missing trade parameters"

    username = parts[1].strip()
    symbol = parts[2].strip().upper()
    action = parts[3].strip().upper()  # BUY or SELL

    try:
        shares = int(parts[4])
        price = float(parts[5])
    except ValueError:
        return "FAIL: Invalid shares quantity or price"

    if shares <= 0 or price <= 0:
        return "FAIL: Shares and price must be positive numbers"

    if action not in ("BUY", "SELL"):
        return "FAIL: Invalid trade action (must be BUY or SELL)"

    total_amount = round(shares * price, 2)

    db = get_db_connection()
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, balance FROM users WHERE username = %s FOR UPDATE",
            (username,),
        )
        user = cursor.fetchone()
        if not user:
            return "FAIL: User not found"

        user_id = user["id"]
        current_balance = float(user["balance"] or 0)

        if action == "BUY":
            if current_balance < total_amount:
                return f"FAIL: Insufficient funds. Available cash: ${current_balance:,.2f}, Required: ${total_amount:,.2f}"

            new_balance = round(current_balance - total_amount, 2)
            cursor.execute("UPDATE users SET balance = %s WHERE id = %s", (new_balance, user_id))

            # בדיקה האם המניה קיימת כבר בתיק
            cursor.execute(
                "SELECT id, shares, average_price FROM user_portfolio WHERE user_id = %s AND symbol = %s",
                (user_id, symbol),
            )
            existing_holding = cursor.fetchone()

            if existing_holding:
                old_shares = int(existing_holding["shares"])
                old_avg = float(existing_holding["average_price"])
                new_shares = old_shares + shares
                new_avg = round(((old_shares * old_avg) + total_amount) / new_shares, 2)
                cursor.execute(
                    "UPDATE user_portfolio SET shares = %s, average_price = %s WHERE id = %s",
                    (new_shares, new_avg, existing_holding["id"]),
                )
            else:
                cursor.execute(
                    "INSERT INTO user_portfolio (user_id, symbol, shares, average_price) VALUES (%s, %s, %s, %s)",
                    (user_id, symbol, shares, price),
                )

            # רישום בטבלת עסקאות
            cursor.execute(
                "INSERT INTO trades (user_id, symbol, action, shares, price, total_amount) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, symbol, "BUY", shares, price, total_amount),
            )
            db.commit()

        elif action == "SELL":
            cursor.execute(
                "SELECT id, shares, average_price FROM user_portfolio WHERE user_id = %s AND symbol = %s FOR UPDATE",
                (user_id, symbol),
            )
            existing_holding = cursor.fetchone()

            if not existing_holding or int(existing_holding["shares"]) < shares:
                owned = int(existing_holding["shares"]) if existing_holding else 0
                return f"FAIL: Insufficient shares. You own {owned} shares of {symbol}, tried to sell {shares}."

            old_shares = int(existing_holding["shares"])
            new_shares = old_shares - shares
            new_balance = round(current_balance + total_amount, 2)

            cursor.execute("UPDATE users SET balance = %s WHERE id = %s", (new_balance, user_id))

            if new_shares == 0:
                cursor.execute("DELETE FROM user_portfolio WHERE id = %s", (existing_holding["id"],))
            else:
                cursor.execute(
                    "UPDATE user_portfolio SET shares = %s WHERE id = %s",
                    (new_shares, existing_holding["id"]),
                )

            # רישום בטבלת עסקאות
            cursor.execute(
                "INSERT INTO trades (user_id, symbol, action, shares, price, total_amount) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, symbol, "SELL", shares, price, total_amount),
            )
            db.commit()

        # שליפת התיק המעודכן
        cursor.execute(
            "SELECT symbol, shares, average_price FROM user_portfolio WHERE user_id = %s AND shares > 0",
            (user_id,),
        )
        updated_portfolio = cursor.fetchall()

        payload = {
            "new_balance": new_balance,
            "portfolio": [
                {
                    "symbol": item["symbol"],
                    "shares": int(item["shares"]),
                    "average_price": float(item["average_price"]),
                }
                for item in updated_portfolio
            ],
        }
        action_verb = "bought" if action == "BUY" else "sold"
        print(f"[LOGIC SERVER] User '{username}' {action_verb} {shares} shares of {symbol} for ${total_amount:,.2f}.")
        return f"SUCCESS|Successfully {action_verb} {shares} shares of {symbol}|" + json.dumps(payload)
    except mysql.connector.Error as err:
        db.rollback()
        print(f"[LOGIC SERVER DATABASE ERROR] Error executing trade: {err}")
        return f"FAIL: Database error ({err})"
    finally:
        db.close()


# ==============================================================================
# 4. ניתוב הבקשות (Request Dispatcher)
# ==============================================================================
def route_request(request_data: str) -> str:
    """
    מפרק את פקודת הלקוח ומנתב אותה לפונקציית הטיפול המתאימה בצורה מוגנת משגיאות.
    """
    if not request_data or not request_data.strip():
        return "FAIL: Empty command"

    parts = request_data.strip().split("|")
    action = parts[0]

    try:
        if action == "LOGIN":
            return handle_login(parts)
        elif action == "REGISTER":
            return handle_register(parts)
        elif action == "REQUEST_CODE":
            return handle_request_code(parts)
        elif action == "RESET_WITH_CODE":
            return handle_reset_with_code(parts)
        elif action == "GET_ADMIN_DATA":
            return handle_get_admin_data()
        elif action == "DELETE_USER":
            return handle_delete_user(parts)
        elif action == "GET_USER_DATA":
            return handle_get_user_data(parts)
        elif action == "EXECUTE_TRADE":
            return handle_execute_trade(parts)
        else:
            return f"FAIL: Unknown command '{action}'"
    except mysql.connector.Error as db_err:
        print(f"[LOGIC SERVER DATABASE ERROR]: {db_err}")
        return "שגיאה: שרת ה-MySQL (ב-XAMPP) אינו מופעל. פתח את לוח הבקרה של XAMPP ולחץ על Start ליד MySQL."
    except Exception as err:
        print(f"[LOGIC SERVER ERROR] Unexpected error handling action '{action}': {err}")
        return f"FAIL: Server internal error ({err})"


# ==============================================================================
# 5. שרת הסוקטים - עבודה בריבוי נימים (Multi-Threaded Socket Server)
# ==============================================================================
def handle_client(client_socket: socket.socket, client_address: Any) -> None:
    """
    מטפל בכל חיבור לקוח בנפרד (חוט ריצה ייעודי - Thread),
    כך שפעולות איטיות (כמו שליחת מייל) לא יחסמו לקוחות אחרים.
    """
    try:
        # קבלת הנתונים מהלקוח (עד 4096 בתים)
        data = client_socket.recv(4096).decode("utf-8")
        if data:
            response = route_request(data)
            client_socket.sendall(response.encode("utf-8"))
    except Exception as err:
        print(f"[LOGIC SERVER ERROR] Communication error with {client_address}: {err}")
    finally:
        client_socket.close()


def start_server() -> None:
    """מפעיל את שרת הסוקטים ומאזין לחיבורים נכנסים מ-main.py."""
    initialize_database()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # מאפשר שימוש חוזר מיידי בפורט במקרה של הפעלה מחדש
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((SERVER_HOST, SERVER_PORT))
    server.listen(10)
    print(f"[LOGIC SERVER] Listening on {SERVER_HOST}:{SERVER_PORT} (Multi-threaded ready)...")

    while True:
        try:
            client_socket, client_address = server.accept()
            # יצירת Thread נפרד עבור כל בקשה נכנסת
            client_thread = threading.Thread(
                target=handle_client,
                args=(client_socket, client_address),
                daemon=True,
            )
            client_thread.start()
        except KeyboardInterrupt:
            print("\n[LOGIC SERVER] Shutting down server gracefully...")
            break
        except Exception as err:
            print(f"[LOGIC SERVER ERROR] Error accepting connection: {err}")

    server.close()


if __name__ == "__main__":
    start_server()
