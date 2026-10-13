import json  # ייבוא ספרייה לעבודה עם פורמט JSON
import os
import random  # ייבוא לייצור קוד אקראי
import smtplib  # ספריית מובנית בפייתון לשליחת מיילים בפרוטוקול SMTP
import socket  # ייבוא ספריית התקשורת המובנית של פייתון לפתיחת ערוץ שיחה מול הפלאסק
from datetime import datetime, timedelta  # ייבוא לחישוב זמן תפוגת הקוד
from email.mime.text import MIMEText  # לעיצוב הודעת המייל
from typing import TypedDict, cast

import mysql.connector  # ייבוא הספרייה שמאפשרת לפייתון לדבר עם בסיס הנתונים MySQL
from werkzeug.security import (  # ייבוא פונקציות להצפנת סיסמאות ובדיקתן
    check_password_hash,
    generate_password_hash,
)


class LoginUser(TypedDict):
    password: str
    fullname: str


class PasswordResetUser(TypedDict):
    reset_code: str | None
    reset_code_expires: datetime | None


# 1. פונקציה ליצירת חיבור לבסיס הנתונים
def get_db_connection():
    return mysql.connector.connect(
        host="localhost",  # השרת המקומי של XAMPP
        user="root",  # משתמש ברירת המחדל ב-XAMPP
        password="",  # הסיסמה הריקה של XAMPP
        database="tradepro_db",  # שם בסיס הנתונים
    )


# 2. יצירה אוטומטית של בסיס הנתונים והטבלאות אם הם עדיין לא קיימים
def initialize_database():
    try:
        # התחברות ראשונית ליצירת בסיס הנתונים במידה ואינו קיים
        admin_db = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
        )
        cursor = admin_db.cursor()
        cursor.execute("CREATE DATABASE IF NOT EXISTS tradepro_db")
        admin_db.commit()
        cursor.close()
        admin_db.close()

        # התחברות לבסיס הנתונים ויצירת הטבלאות
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(255) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                fullname VARCHAR(255) NOT NULL,
                balance DECIMAL(12,2) DEFAULT 10000.00,
                reset_code VARCHAR(10) NULL,
                reset_code_expires DATETIME NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

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

        db.commit()
        cursor.close()
        db.close()
        print("[LOGIC SERVER] Database initialized successfully.")
    except mysql.connector.Error as err:
        print(f"[LOGIC SERVER ERROR] Failed to initialize database: {err}")
        raise


# 3. פונקציית עזר לשליחת מייל איפוס סיסמה
def send_otp_email(target_email, code):
    sender_email = os.environ.get("TRADEPRO_EMAIL")
    sender_password = os.environ.get("TRADEPRO_EMAIL_PASSWORD")
    if not sender_email or not sender_password:
        raise RuntimeError(
            "TRADEPRO_EMAIL and TRADEPRO_EMAIL_PASSWORD must be configured"
        )

    msg = MIMEText(
        f"קוד איפוס הסיסמה שלך למערכת TradePro הוא: {code}\nהקוד תקף ל-10 דקות בלבד."
    )
    msg["Subject"] = "קוד לאיפוס סיסמה - TradePro"
    msg["From"] = sender_email
    msg["To"] = target_email

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, target_email, msg.as_string())


# 4. הפונקציה המרכזית שמפעילה את השרת
def start_server():
    initialize_database()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 9999))
    server.listen(5)
    print("[LOGIC SERVER] Server is running and listening on port 9999...")

    while True:
        client_socket, _ = server.accept()
        data = client_socket.recv(1024).decode("utf-8")

        if not data:
            client_socket.close()
            continue

        parts = data.split("|")
        action = parts[0]

        db = None
        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)

            # --- LOGIN ---
            if action == "LOGIN":
                username = parts[1]
                password_entered = parts[2]

                cursor.execute(
                    "SELECT id, password, fullname FROM users WHERE username = %s",
                    (username,),
                )
                user = cast(LoginUser | None, cursor.fetchone())

                if user and check_password_hash(
                    user["password"], password_entered
                ):
                    response = f"SUCCESS|{user['fullname']}"
                    print(
                        f"[LOGIC SERVER] User logged in successfully: {username}"
                    )
                    cursor.execute(
                        "INSERT INTO login_logs (username, status) VALUES (%s, %s)",
                        (username, "SUCCESS"),
                    )
                    db.commit()
                else:
                    response = "FAIL: Invalid username or password"
                    print(f"[LOGIC SERVER] Login failed for: {username}")
                    cursor.execute(
                        "INSERT INTO login_logs (username, status) VALUES (%s, %s)",
                        (username, "FAILED"),
                    )
                    db.commit()

            # --- REQUEST_CODE ---
            elif action == "REQUEST_CODE":
                username = parts[1]

                cursor.execute(
                    "SELECT id FROM users WHERE username = %s", (username,)
                )
                if not cursor.fetchone():
                    response = "FAIL: Email address not found"
                else:
                    otp_code = str(random.randint(100000, 999999))
                    expires_at = datetime.now() + timedelta(minutes=10)

                    query = "UPDATE users SET reset_code = %s, reset_code_expires = %s WHERE username = %s"
                    cursor.execute(query, (otp_code, expires_at, username))
                    db.commit()

                    try:
                        send_otp_email(username, otp_code)
                        print(f"[LOGIC SERVER] OTP Sent to {username}")
                        response = "SUCCESS"
                    except Exception as e:
                        print(f"[LOGIC SERVER ERROR] Email send failed: {e}")
                        response = "FAIL: Failed to send email"

            # --- RESET_WITH_CODE ---
            elif action == "RESET_WITH_CODE":
                username = parts[1]
                code_entered = parts[2]
                new_password = parts[3]

                cursor.execute(
                    "SELECT reset_code, reset_code_expires FROM users WHERE username = %s",
                    (username,),
                )
                user_data = cast(PasswordResetUser | None, cursor.fetchone())

                if not user_data:
                    response = "FAIL: No reset request found"
                elif not user_data["reset_code"] or user_data["reset_code_expires"] is None:
                    response = "FAIL: No reset request found"
                elif user_data["reset_code"] != code_entered:
                    response = "FAIL: Invalid code"
                elif datetime.now() > user_data["reset_code_expires"]:
                    response = "FAIL: Code has expired"
                else:
                    hashed_pass = generate_password_hash(new_password)
                    update_query = """
                        UPDATE users
                        SET password = %s, reset_code = NULL, reset_code_expires = NULL
                        WHERE username = %s
                    """
                    cursor.execute(update_query, (hashed_pass, username))
                    db.commit()
                    response = "SUCCESS"

            # --- REGISTER ---
            elif action == "REGISTER":
                fullname = parts[1]
                username = parts[2]
                raw_password = parts[3]

                cursor.execute(
                    "SELECT id FROM users WHERE username = %s", (username,)
                )
                if cursor.fetchone():
                    response = "FAIL: Username already exists"
                else:
                    hashed_password = generate_password_hash(raw_password)
                    query = "INSERT INTO users (username, password, fullname, balance) VALUES (%s, %s, %s, %s)"
                    cursor.execute(
                        query, (username, hashed_password, fullname, 10000.00)
                    )
                    db.commit()
                    print(
                        f"[LOGIC SERVER] New User Registered in MySQL: {username}"
                    )
                    response = "SUCCESS"

            # --- GET_ADMIN_DATA ---
            elif action == "GET_ADMIN_DATA":
                cursor.execute(
                    "SELECT id, username, fullname, balance FROM users"
                )
                users = cursor.fetchall()

                cursor.execute(
                    "SELECT username, status, DATE_FORMAT(login_time, '%Y-%m-%d %H:%i:%s') as login_time FROM login_logs ORDER BY id DESC LIMIT 20"
                )
                logs = cursor.fetchall()

                admin_data = {"users": users, "logs": logs}
                response = "SUCCESS|" + json.dumps(admin_data, default=str)

            else:
                response = "FAIL: Unknown command"

        except mysql.connector.Error as err:
            print(f"[LOGIC SERVER ERROR] Database Error: {err}")
            response = "FAIL: Database connection error"

        finally:
            if db and db.is_connected():
                cursor.close()
                db.close()

        client_socket.send(response.encode("utf-8"))
        client_socket.close()


if __name__ == "__main__":
    start_server()