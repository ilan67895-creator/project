import socket

# הגדרות שרת הסוקט שלנו
HOST = '127.0.0.1'
PORT = 8888  # פורט שונה מזה של Flask

def start_server():
    # יצירת סוקט TCP
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen(5)
    print(f"[*] Logic Server is listening on {HOST}:{PORT}")

    while True:
        client_socket, addr = server.accept()
        print(f"[*] Connection received from {addr}")
        
        # קבלת המידע מהלקוח (עד 1024 בתים)
        data = client_socket.recv(1024).decode('utf-8')
        
        if data:
            print(f"[*] Received Data: {data}")
            # הפרוטוקול שלנו: אנו מצפים לקבל מחרוזת בסגנון "LOGIN|username|password"
            parts = data.split('|')
            action = parts[0]
            
            if action == "LOGIN":
                username = parts[1]
                password = parts[2]
                
                # כאן בהמשך תחבר את ה-DB. כרגע נשתמש בבדיקה פשוטה:
                if username == "admin@tradepro.com" and password == "123456":
                    client_socket.send("SUCCESS".encode('utf-8'))
                else:
                    client_socket.send("FAIL".encode('utf-8'))
        
        # סגירת החיבור הספציפי לאחר סיום הבקשה
        client_socket.close()

if __name__ == "__main__":
    start_server()