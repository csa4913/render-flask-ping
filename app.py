# app.py  (Render 서버용 공용 DB + REST API + 검색 + 첨부파일 관리)

import os
import sqlite3
import json
import shutil
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ------------------------------
#  환경 설정
# ------------------------------

# SQLite DB 파일 경로 (프로젝트 폴더 안에 생성)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "orders.db")

# 첨부파일 업로드 루트 폴더
UPLOAD_ROOT = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_ROOT, exist_ok=True)


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
#  발주 목록 조회 (+ 검색 q)
# ------------------------------
@app.route("/orders", methods=["GET"])
def list_orders():
    """
    목록 조회
    - ?q= 검색어 가 있으면 po_number 또는 data(JSON 문자열)에서 LIKE 검색
    응답: [ { ...행 데이터... }, ... ]
    """
    q = request.args.get("q", "").strip()

    conn = get_conn()
    cur = conn.cursor()

    if q:
        like = f"%{q}%"
        cur.execute(
            """
            SELECT * FROM orders
            WHERE po_number LIKE ?
               OR data      LIKE ?
            ORDER BY id DESC
            """,
            (like, like),
        )
    else:
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
#  발주 삭제 (DB + 첨부파일 폴더)
# ------------------------------
@app.route("/orders/<int:order_id>", methods=["DELETE"])
def delete_order(order_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()

    # 첨부파일 폴더도 같이 삭제
    folder = os.path.join(UPLOAD_ROOT, str(order_id))
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)

    if deleted == 0:
        return jsonify({"error": "not_found"}), 404

    return jsonify({"status": "deleted", "id": order_id}), 200


# ------------------------------
#  첨부파일 업로드
#  (EXE에서 /orders/<id>/files 로 파일 전송)
# ------------------------------
@app.route("/orders/<int:order_id>/files", methods=["POST"])
def upload_files(order_id: int):
    """
    multipart/form-data 로 파일 업로드:
      필드 이름: invoice_file, workconfirm_file, inspect_file, extra_pdf_file
    업로드 후 data(JSON) 안의 해당 필드를
      "/files/<id>/<저장파일명>" URL로 업데이트
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    # 기존 data(JSON) 파싱
    data = row_to_dict(row)
    # row_to_dict 는 id, row_version, created_at, updated_at 도 포함하므로
    # 실제 저장용 payload 에선 제거
    meta_keys = {"id", "row_version", "created_at", "updated_at"}
    payload = {k: v for k, v in data.items() if k not in meta_keys}

    upload_dir = os.path.join(UPLOAD_ROOT, str(order_id))
    os.makedirs(upload_dir, exist_ok=True)

    file_fields = ["invoice_file", "workconfirm_file", "inspect_file", "extra_pdf_file"]
    updated_files = {}

    for field in file_fields:
        file = request.files.get(field)
        if not file or not file.filename:
            continue

        original_name = file.filename
        safe_name = f"{field}_{original_name}"
        save_path = os.path.join(upload_dir, safe_name)
        file.save(save_path)

        # 클라이언트가 접근할 URL(상대 경로) 저장
        url_path = f"/files/{order_id}/{safe_name}"
        payload[field] = url_path
        updated_files[field] = url_path

    # 파일이 하나도 없으면 그냥 OK 반환
    if not updated_files:
        conn.close()
        return jsonify({"status": "no_files"}), 200

    now = datetime.utcnow().isoformat()

    # po_number 갱신 (payload에 있다고 가정)
    po_number = str(payload.get("po_number", "")).strip()

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
    conn.close()

    return jsonify({"status": "ok", "files": updated_files}), 200


# ------------------------------
#  업로드된 파일 제공
# ------------------------------
@app.route("/files/<int:order_id>/<path:filename>", methods=["GET"])
def serve_file(order_id: int, filename: str):
    """
    /files/<id>/<파일명> 으로 접근 시
    uploads/<id>/<파일명> 에서 파일 제공
    """
    upload_dir = os.path.join(UPLOAD_ROOT, str(order_id))
    return send_from_directory(upload_dir, filename, as_attachment=False)


# ------------------------------
#  Render / 로컬 실행 진입점
# ------------------------------
if __name__ == "__main__":
    # 로컬 테스트 용
    app.run(host="0.0.0.0", port=5000, debug=True)
