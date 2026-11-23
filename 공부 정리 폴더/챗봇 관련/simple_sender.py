import socket
import time

def main():
    print("[시스템] 송신기를 시작합니다. (10초 주기)")
    while True:
        message = "hello world"
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect(("127.0.0.1", 9000))
                s.sendall(message.encode("utf-8"))
            print(f"[전송 완료] {message}")
        except ConnectionRefusedError:
            print("[오류] 서버에 연결할 수 없습니다. simple_receiver.py가 켜져 있는지 확인하세요.")
            
        time.sleep(10)

if __name__ == "__main__":
    main()