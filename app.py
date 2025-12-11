# app.py  (Render 서버용 공용 DB + REST API)

import os
import sqlite3
import json
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS

# ------------------------------
#  환경 설정
# ------------------------------

# SQLite DB 파일 경로 (프로젝트 폴더 안에 생성)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "orders.db")


def get_conn():
    """SQLite 커넥션 생성 (dict 형태로 결과 받기 위해 row_factory 설정)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """최초 1회: orders 테이블 생성"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            po_number   TEXT,
            data        TEXT NOT NULL,
            row_version INTEGER DEFAULT 1,
            created_at  TEXT,
            updated_at  TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    """orders 테이블 1행(Row)을 클라이언트에 줄 dict 형태로 변환"""
    # data 컬럼(JSON 문자열) 파싱
    try:
        data = json.loads(row["data"])
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    # 메타 정보(id, row_version, created_at, updated_at) 추가
    data["id"] = row["id"]
    data["row_version"] = row["row_version"]
    data["created_at"] = row["created_at"]
    data["updated_at"] = row["updated_at"]

    return data


# Flask 앱 생성 및 CORS 허용
app = Flask(__name__)
CORS(app)

# 서버 시작 시 DB 초기화
init_db()


# ------------------------------
#  기본 체크용 엔드포인트
# ------------------------------
@app.route("/ping", methods=["GET"])
def ping():
    return "pong", 200


# ------------------------------
#  발주 목록 조회
# ------------------------------
@app.route("/orders", methods=["GET"])
def list_orders():
    """
    전체 목록 조회
    응답: [ { ...행 데이터... }, ... ]
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    result = [row_to_dict(r) for r in rows]
    return jsonify(result), 200


# ------------------------------
#  단일 발주 조회
# ------------------------------
@app.route("/orders/<int:order_id>", methods=["GET"])
def get_order(order_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "not_found"}), 404

    return jsonify(row_to_dict(row)), 200


# ------------------------------
#  새 발주 생성
# ------------------------------
@app.route("/orders", methods=["POST"])
def create_order():
    """
    요청 body(JSON): { "po_number": "...", 그 외 필드들... }
    응답: 저장된 전체 행( id, row_version 등 포함 ) JSON
    """
    payload = request.get_json(silent=True) or {}

    # po_number는 별도 컬럼에 저장(검색/정렬 용도)
    po_number = str(payload.get("po_number", "")).strip()

    now = datetime.utcnow().isoformat()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO orders (po_number, data, row_version, created_at, updated_at)
        VALUES (?, ?, 1, ?, ?)
        """,
        (po_number, json.dumps(payload, ensure_ascii=False), now, now),
    )
    new_id = cur.lastrowid
    conn.commit()

    # 다시 조회해서 클라이언트에 돌려줌
    cur.execute("SELECT * FROM orders WHERE id = ?", (new_id,))
    row = cur.fetchone()
    conn.close()

    return jsonify(row_to_dict(row)), 201


# ------------------------------
#  기존 발주 수정
# ------------------------------
@app.route("/orders/<int:order_id>", methods=["PUT"])
def update_order(order_id: int):
    """
    요청 body(JSON): { ...전체 필드..., "row_version": 현재버전 }
    - row_version 이 맞지 않으면 409(CONFLICT) 에러 리턴
    """
    payload = request.get_json(silent=True) or {}

    sent_version = payload.pop("row_version", None)
    po_number = str(payload.get("po_number", "")).strip()

    conn = get_conn()
    cur = conn.cursor()

    # 현재 버전 조회
    cur.execute("SELECT row_version FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    db_version = row["row_version"]

    # 낙관적 잠금(동시 수정 방지)
    if sent_version is not None and int(sent_version) != db_version:
        conn.close()
        return (
            jsonify(
                {
                    "error": "version_conflict",
                    "message": "row_version does not match. reload first.",
                    "db_version": db_version,
                }
            ),
            409,
        )

    now = datetime.utcnow().isoformat()

    cur.execute(
        """
        UPDATE orders
        SET po_number = ?,
            data       = ?,
            row_version = row_version + 1,
            updated_at = ?
        WHERE id = ?
        """,
        (po_number, json.dumps(payload, ensure_ascii=False), now, order_id),
    )
    conn.commit()

    # 수정된 행 다시 조회
    cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    row2 = cur.fetchone()
    conn.close()

    return jsonify(row_to_dict(row2)), 200


# ------------------------------
#  발주 삭제
# ------------------------------
@app.route("/orders/<int:order_id>", methods=["DELETE"])
def delete_order(order_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()

    if deleted == 0:
        return jsonify({"error": "not_found"}), 404

    return jsonify({"status": "deleted", "id": order_id}), 200


# ------------------------------
#  Render / 로컬 실행 진입점
# ------------------------------
if __name__ == "__main__":
    # 로컬 테스트 용
    app.run(host="0.0.0.0", port=5000, debug=True)
