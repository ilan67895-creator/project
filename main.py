from flask import Flask, render_template, request
import socket

app = Flask(__name__)

# פונקציה שמציגה את מסך ההתחברות
@app.route('/')
def home():
    return render_template('index.html')

# פונקציה שתופסת את הנתונים מהטופס ופונה לשרת הסוקט
@app.route('/login', methods=['POST'])
def login():
    user = request.form.get('username')
    pwd = request.form.get('password')
    
    try:
        # --- תחילת תקשורת ה-Socket פנימית ---
        # אנחנו יוצרים סוקט ומתחברים לשרת הלוגיקה שלנו שרץ בפורט 8888
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('127.0.0.1', 8888))
        
        # מרכיבים את ההודעה לפי הפרוטוקול שהמצאנו ושולחים
        message = f"LOGIN|{user}|{pwd}"
        client_socket.send(message.encode('utf-8'))
        
        # מקבלים את התשובה משרת הלוגיקה (SUCCESS או FAIL)
        response = client_socket.recv(1024).decode('utf-8')
        client_socket.close()
        # --- סיום תקשורת ה-Socket ---
        
        if response == "SUCCESS":
            return f"<h1>Login Successful! Welcome {user}</h1>"
        else:
            return "<h1>Login Failed! Incorrect credentials.</h1>"
            
    except ConnectionRefusedError:
        return "<h1>Error: The Logic Socket Server is not running!</h1>"

if __name__ == '__main__':
    app.run(debug=True, port=5000)