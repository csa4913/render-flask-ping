from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "Hello from Render!"

@app.route("/ping")
def ping():
    return "pong"

if __name__ == "__main__":
    # 로컬 테스트용
    app.run(host="0.0.0.0", port=5000, debug=True)
