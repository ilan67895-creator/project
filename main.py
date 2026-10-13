from flask import Flask, render_template, request, redirect, url_for # ייבוא רכיבי פלאסק להצגת דפי רשת וקבלת נתונים
import socket # ייבוא התקשורת לדבר עם שרת הלוגיקה
import json # ייבוא ספריית JSON לעבודה עם פורמט JSON
app = Flask(__name__) # הגדרת הפרויקט של האתר

# פונקציית עזר שמתחברת לשרת הלוגיקה, מעבירה הודעה ומחזירה תשובה
def send_to_logic_server(message):
    try:
        # פתיחת חלון שיחה חדש מול שרת הלוגיקה
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('127.0.0.1', 9999)) # התחברות לפורט 9999 בו השרת השני מאזין

        # שליחת ההודעה מומרת לבינארי
        client_socket.send(message.encode('utf-8'))
        # קבלת התשובה חזרה
        response = client_socket.recv(1024).decode('utf-8')

        client_socket.close() # סגירת ערוץ השיחה
        return response
    except Exception as e:
        # טיפול במצב שבו שרת הלוגיקה סגור
        return "Error: The Logic Socket Server is not running!"

# 1. הנתיב הראשי של האתר - מציג את דף ההתחברות
@app.route('/')
def index():
    return render_template('index.html')

# 2. נתיב קבלת טופס ההתחברות
@app.route('/login', methods=['POST'])
def login():
    # שליפת הנתונים מטופס ה-HTML (לפי מאפיין name של ה-input)
    username = request.form.get('username')
    password = request.form.get('password')

    # שליחת בקשת ההתחברות לשרת הלוגיקה
    response = send_to_logic_server(f"LOGIN|{username}|{password}")

    # אם ההתחברות הצליחה (התשובה מתחילה במילה SUCCESS)
    if response.startswith("SUCCESS"):
        # חיתוך התשובה כדי לשלוף את השם המלא ששרת הלוגיקה החזיר
        fullname = response.split("|")[1] if "|" in response else username
        return f"<h1>ברוך הבא, {fullname}! התחברת למערכת TradePro בהצלחה.</h1>"
    else:
        # אם ההתחברות נכשלה, נחזיר את המשתמש לדף ההתחברות ונעביר לו הודעת שגיאה
        return render_template('index.html', error="שם משתמש או סיסמה שגויים, אנא נסה שנית.")

# 3. דף ההרשמה - גם להצגת הטופס (GET) וגם לשליחת נתונים (POST)
@app.route('/register', methods=['GET', 'POST'])
def register():
    # אם לחצו על שלח (POST)
    if request.method == 'POST':
        fullname = request.form.get('fullname')
        username = request.form.get('username')
        password = request.form.get('password')

        # שליחת בקשת יצירת משתמש עם הפרוטוקול שבנינו
        response = send_to_logic_server(f"REGISTER|{fullname}|{username}|{password}")

        if "SUCCESS" in response:
            return f"<h1>Registration Successful! <a href='/'>Click here to Login</a></h1>"
        else:
            # אם שרת הלוגיקה החזיר שגיאה, נציג שוב את דף ההרשמה עם הודעת השגיאה באדום
            return render_template('register.html', error=response)

    # אם רק נכנסו לדף ההרשמה (GET), נציג אותו ריק
    return render_template('register.html')

# 4. שלב 1: הזנת מייל לקבלת קוד איפוס
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')

        # שליחת בקשה לשרת הלוגיקה ליצירת קוד ושליחתו
        response = send_to_logic_server(f"REQUEST_CODE|{email}")

        if "SUCCESS" in response:
            # אם הקוד נשלח בהצלחה, מעבירים את המשתמש לדף הזנת הקוד ביחד עם המייל שלו
            return redirect(url_for('reset_password', email=email))
        else:
            return render_template('forgot_password.html', error=response)

    return render_template('forgot_password.html')

# 5. שלב 2: הזנת הקוד שנתקבל במייל והגדרת סיסמה חדשה
@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    email = request.args.get('email') # שליפת המייל מפרמטר ה-URL

    if request.method == 'POST':
        email = request.form.get('email')
        code = request.form.get('code')
        new_password = request.form.get('new_password')

        # שליחת הקוד והסיסמה החדשה לאימות ועדכון בשרת הלוגיקה
        response = send_to_logic_server(f"RESET_WITH_CODE|{email}|{code}|{new_password}")

        if "SUCCESS" in response:
            return f"<h1>Password reset successful! <a href='/'>Click here to Login</a></h1>"
        else:
            return render_template('reset_password.html', email=email, error=response)

    return render_template('reset_password.html', email=email)
# 6. דף מנהל - מציג נתוני משתמשים ויומני פעילות
@app.route('/admin')
def admin_dashboard():
    # שליחת בקשה לקבלת נתוני הניהול משרת הלוגיקה
    raw_response = send_to_logic_server("GET_ADMIN_DATA")

    if raw_response.startswith("SUCCESS"):
        # פירוק התשובה והמרת מחרוזת ה-JSON חזרה למילון פייתון
        json_str = raw_response.split("|", 1)[1]
        data = json.loads(json_str)
        return render_template('admin.html', users=data['users'], logs=data['logs'])
    else:
        return f"<h1>שגיאה שטעינת נתוני מנהל: {raw_response}</h1>"
# הרצת שרת האינטרנט עצמו בפורט 5000
if __name__ == '__main__':
    app.run(debug=True, port=5000)