import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, abort
import psycopg2
import psycopg2.extras

APP_TZ = "Asia/Seoul"

ATTACH_TYPES = ["invoice", "work", "inspect", "other"]
ATTACH_LABEL = {
    "invoice": "인보이스",
    "work": "작업확인서",
    "inspect": "검수서",
    "other": "기타 서류",
}

def create_app():
    app = Flask(__name__)

    # Render에서는 영구 디스크가 기본이 아니므로 /tmp 권장
    upload_root = os.environ.get("UPLOAD_DIR", "/tmp/uploads")
    os.makedirs(upload_root, exist_ok=True)
    app.config["UPLOAD_DIR"] = upload_root

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required (Render PostgreSQL).")

    def db_conn():
        # Render postgres url은 ssl 필요인 경우가 많음
        return psycopg2.connect(database_url, sslmode=os.environ.get("DB_SSLMODE", "require"))

    def init_db():
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                CREATE TABLE IF NOT EXISTS rows (
                    id UUID PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    title TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '기타',
                    note TEXT NOT NULL DEFAULT ''
                );
                """)
                cur.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id UUID PRIMARY KEY,
                    row_id UUID NOT NULL REFERENCES rows(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    size_bytes BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """)
            conn.commit()

    init_db()

    @app.get("/")
    def home():
        return render_template("index.html", attach_label=ATTACH_LABEL)

    @app.get("/api/rows")
    def list_rows():
        group = request.args.get("group", "time")  # time | kind
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT r.id, r.created_at, r.title, r.category, r.note
                    FROM rows r
                    ORDER BY r.created_at DESC
                """)
                rows = cur.fetchall()

                # 파일도 같이 가져오기
                cur.execute("""
                    SELECT f.id, f.row_id, f.kind, f.original_name, f.stored_name, f.size_bytes, f.created_at
                    FROM files f
                    ORDER BY f.created_at DESC
                """)
                files = cur.fetchall()

        file_map = {}
        for f in files:
            file_map.setdefault(str(f["row_id"]), {}).setdefault(f["kind"], []).append(f)

        # rows에 files 붙이기
        for r in rows:
            rid = str(r["id"])
            r["files"] = file_map.get(rid, {})

        if group == "kind":
            grouped = {}
            for r in rows:
                grouped.setdefault(r["category"], []).append(r)
            return jsonify({"mode": "kind", "groups": grouped})

        return jsonify({"mode": "time", "rows": rows})

    @app.post("/api/rows")
    def create_row():
        data = request.get_json(force=True, silent=True) or {}
        title = (data.get("title") or "").strip()
        category = (data.get("category") or "기타").strip()
        note = (data.get("note") or "").strip()

        if not title:
            return jsonify({"ok": False, "error": "제목을 입력해주세요."}), 400

        row_id = uuid.uuid4()
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO rows (id, title, category, note)
                    VALUES (%s, %s, %s, %s)
                """, (row_id, title, category, note))
            conn.commit()

        return jsonify({"ok": True, "id": str(row_id)})

    @app.delete("/api/rows/<row_id>")
    def delete_row(row_id):
        try:
            rid = uuid.UUID(row_id)
        except ValueError:
            return jsonify({"ok": False, "error": "잘못된 ID"}), 400

        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # 파일 목록 먼저 가져와서 서버 파일 삭제
                cur.execute("SELECT stored_name FROM files WHERE row_id=%s", (rid,))
                to_delete = cur.fetchall()

                cur.execute("DELETE FROM rows WHERE id=%s", (rid,))
            conn.commit()

        # 저장 파일 삭제 시도 (없어도 무시)
        for f in to_delete:
            p = os.path.join(app.config["UPLOAD_DIR"], f["stored_name"])
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

        return jsonify({"ok": True})

    @app.post("/api/upload")
    def upload_file():
        """
        form-data:
          row_id: uuid
          kind: invoice|work|inspect|other
          file: <binary>
        """
        row_id = request.form.get("row_id", "").strip()
        kind = request.form.get("kind", "").strip()

        if kind not in ATTACH_TYPES:
            return jsonify({"ok": False, "error": "첨부 종류가 올바르지 않습니다."}), 400

        try:
            rid = uuid.UUID(row_id)
        except ValueError:
            return jsonify({"ok": False, "error": "row_id가 올바르지 않습니다."}), 400

        if "file" not in request.files:
            return jsonify({"ok": False, "error": "파일이 없습니다."}), 400

        f = request.files["file"]
        if not f.filename:
            return jsonify({"ok": False, "error": "파일명이 없습니다."}), 400

        stored_name = f"{uuid.uuid4().hex}_{os.path.basename(f.filename)}"
        save_path = os.path.join(app.config["UPLOAD_DIR"], stored_name)
        f.save(save_path)

        size_bytes = 0
        try:
            size_bytes = os.path.getsize(save_path)
        except Exception:
            pass

        file_id = uuid.uuid4()
        with db_conn() as conn:
            with conn.cursor() as cur:
                # row 존재 확인
                cur.execute("SELECT 1 FROM rows WHERE id=%s", (rid,))
                if cur.fetchone() is None:
                    return jsonify({"ok": False, "error": "해당 행이 존재하지 않습니다."}), 404

                cur.execute("""
                    INSERT INTO files (id, row_id, kind, original_name, stored_name, size_bytes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (file_id, rid, kind, f.filename, stored_name, size_bytes))
            conn.commit()

        return jsonify({"ok": True, "file_id": str(file_id)})

    @app.get("/api/download/<file_id>")
    def download_file(file_id):
        try:
            fid = uuid.UUID(file_id)
        except ValueError:
            abort(404)

        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT stored_name, original_name FROM files WHERE id=%s", (fid,))
                row = cur.fetchone()
                if not row:
                    abort(404)

        # Render 임시 디스크에 없으면 404 (MVP 한계)
        stored = row["stored_name"]
        original = row["original_name"]
        folder = app.config["UPLOAD_DIR"]
        path = os.path.join(folder, stored)
        if not os.path.exists(path):
            abort(404)

        return send_from_directory(folder, stored, as_attachment=True, download_name=original)

    @app.delete("/api/files/<file_id>")
    def delete_file(file_id):
        try:
            fid = uuid.UUID(file_id)
        except ValueError:
            return jsonify({"ok": False, "error": "잘못된 ID"}), 400

        stored_name = None
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT stored_name FROM files WHERE id=%s", (fid,))
                row = cur.fetchone()
                if not row:
                    return jsonify({"ok": False, "error": "파일이 없습니다."}), 404
                stored_name = row["stored_name"]
                cur.execute("DELETE FROM files WHERE id=%s", (fid,))
            conn.commit()

        # 실제 파일 삭제 (없으면 무시)
        try:
            p = os.path.join(app.config["UPLOAD_DIR"], stored_name)
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

        return jsonify({"ok": True})

    return app

app = create_app()

if __name__ == "__main__":
    # 로컬 테스트
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
