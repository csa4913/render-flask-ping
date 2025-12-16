# app.py
# Render 배포용 - 공용 DB + REST API + 파일 업로드 (B방식 최종본)

import os
import json
import sqlite3
import shutil
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ======================================================
# 기본 설정
# ======================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, "orders.db")

UPLOAD_ROOT = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_ROOT, exist_ok=True)


# ======================================================
# DB 유틸
# ======================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
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
    try:
        data = json.loads(row["data"])
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    data["id"] = row["id"]
    data["row_version"] = row["row_version"]
    data["created_at"] = row["created_at"]
    data["updated_at"] = row["updated_at"]
    return data


# ======================================================
# Flask App
# ======================================================

app = Flask(__name__)
CORS(app)

init_db()


# ======================================================
# 기본 체크
# ======================================================

@app.route("/ping", methods=["GET"])
def ping():
    return "pong", 200


# ======================================================
# 발주 목록 조회 + 검색
# ======================================================

@app.route("/orders", methods=["GET"])
def list_orders():
    q = request.args.get("q", "").strip()

    conn = get_conn()
    cur = conn.cursor()

    if q:
        like = f"%{q}%"
        cur.execute(
            """
            SELECT * FROM orders
            WHERE po_number LIKE ?
               OR data LIKE ?
            ORDER BY id DESC
            """,
            (like, like),
        )
    else:
        cur.execute("SELECT * FROM orders ORDER BY id DESC")

    rows = cur.fetchall()
    conn.close()

    return jsonify([row_to_dict(r) for r in rows]), 200


# ======================================================
# 단일 발주 조회
# ======================================================

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


# ======================================================
# 새 발주 생성
# ======================================================

@app.route("/orders", methods=["POST"])
def create_order():
    payload = request.get_json(silent=True) or {}

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

    cur.execute("SELECT * FROM orders WHERE id = ?", (new_id,))
    row = cur.fetchone()
    conn.close()

    return jsonify(row_to_dict(row)), 201


# ======================================================
# 기존 발주 수정 (row_version 충돌 방지)
# ======================================================

@app.route("/orders/<int:order_id>", methods=["PUT"])
def update_order(order_id: int):
    payload = request.get_json(silent=True) or {}

    sent_version = payload.pop("row_version", None)
    po_number = str(payload.get("po_number", "")).strip()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT row_version FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    db_version = row["row_version"]

    if sent_version is not None and int(sent_version) != db_version:
        conn.close()
        return jsonify({
            "error": "version_conflict",
            "db_version": db_version
        }), 409

    now = datetime.utcnow().isoformat()

    cur.execute(
        """
        UPDATE orders
        SET po_number = ?,
            data = ?,
            row_version = row_version + 1,
            updated_at = ?
        WHERE id = ?
        """,
        (po_number, json.dumps(payload, ensure_ascii=False), now, order_id),
    )
    conn.commit()

    cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    updated = cur.fetchone()
    conn.close()

    return jsonify(row_to_dict(updated)), 200


# ======================================================
# 발주 삭제 + 첨부파일 삭제
# ======================================================

@app.route("/orders/<int:order_id>", methods=["DELETE"])
def delete_order(order_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()

    folder = os.path.join(UPLOAD_ROOT, str(order_id))
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)

    if deleted == 0:
        return jsonify({"error": "not_found"}), 404

    return jsonify({"status": "deleted", "id": order_id}), 200


# ======================================================
# 첨부파일 업로드 (B방식 핵심)
# ======================================================

@app.route("/orders/<int:order_id>/files", methods=["POST"])
def upload_files(order_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    data = row_to_dict(row)
    meta = {"id", "row_version", "created_at", "updated_at"}
    payload = {k: v for k, v in data.items() if k not in meta}

    upload_dir = os.path.join(UPLOAD_ROOT, str(order_id))
    os.makedirs(upload_dir, exist_ok=True)

    file_fields = [
        "invoice_file",
        "workconfirm_file",
        "inspect_file",
        "extra_pdf_file",
    ]

    updated = {}

    for field in file_fields:
        file = request.files.get(field)
        if not file or not file.filename:
            continue

        safe_name = f"{field}_{file.filename}"
        save_path = os.path.join(upload_dir, safe_name)
        file.save(save_path)

        url_path = f"/files/{order_id}/{safe_name}"
        payload[field] = url_path
        updated[field] = url_path

    if not updated:
        conn.close()
        return jsonify({"status": "no_files"}), 200

    now = datetime.utcnow().isoformat()
    po_number = str(payload.get("po_number", "")).strip()

    cur.execute(
        """
        UPDATE orders
        SET po_number = ?,
            data = ?,
            row_version = row_version + 1,
            updated_at = ?
        WHERE id = ?
        """,
        (po_number, json.dumps(payload, ensure_ascii=False), now, order_id),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "ok", "files": updated}), 200


# ======================================================
# 파일 제공
# ======================================================

@app.route("/files/<int:order_id>/<path:filename>", methods=["GET"])
def serve_file(order_id: int, filename: str):
    upload_dir = os.path.join(UPLOAD_ROOT, str(order_id))
    return send_from_directory(upload_dir, filename, as_attachment=False)


# ======================================================
# Render 실행 진입점
# ======================================================

if __name__ == "__main__":
    # 로컬 테스트용
    app.run(host="0.0.0.0", port=5000, debug=True)
