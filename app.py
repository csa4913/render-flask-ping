import os
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

# =========================
# Flask / DB 설정
# =========================
app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# Render 보정
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", 50 * 1024 * 1024))

db = SQLAlchemy(app)

# =========================
# 파일 저장소 (Render는 휘발성)
# =========================
DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/data"))
UPLOAD_ROOT = DATA_DIR / "uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

# =========================
# 모델 정의 (기존 SQLite 스키마 그대로)
# =========================
class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"

    id = db.Column(db.Integer, primary_key=True)

    vendor = db.Column(db.Text)
    site = db.Column(db.Text)
    po_number = db.Column(db.Text)
    quantity = db.Column(db.Integer)

    q_code = db.Column(db.Text)
    order_date = db.Column(db.Text)
    due_date = db.Column(db.Text)
    manager = db.Column(db.Text)
    invoice_no = db.Column(db.Text)
    contract_po = db.Column(db.Text)

    explosion_proof = db.Column(db.Text)
    contract_settle = db.Column(db.Text)
    self_test = db.Column(db.Text)
    env_cert = db.Column(db.Text)
    worker = db.Column(db.Text)
    material = db.Column(db.Text)
    area = db.Column(db.Text)
    status = db.Column(db.Text)
    release_cond = db.Column(db.Text)

    region = db.Column(db.Text)
    division = db.Column(db.Text)
    line = db.Column(db.Text)
    floor = db.Column(db.Text)
    bay = db.Column(db.Text)

    pillar_no = db.Column(db.Text)
    control_no = db.Column(db.Text)
    process = db.Column(db.Text)
    equip_name = db.Column(db.Text)
    panel_sn = db.Column(db.Text)
    maker = db.Column(db.Text)
    equip_model = db.Column(db.Text)
    equip_no = db.Column(db.Text)

    agent_type = db.Column(db.Text)
    agent_amount = db.Column(db.Text)
    smoke_detector = db.Column(db.Text)
    o2_detector = db.Column(db.Text)
    operation_type = db.Column(db.Text)
    manual_box = db.Column(db.Text)

    remark = db.Column(db.Text)

    invoice_file = db.Column(db.Text)
    workconfirm_file = db.Column(db.Text)
    inspect_file = db.Column(db.Text)
    extra_pdf_file = db.Column(db.Text)

    row_version = db.Column(db.Integer, default=1)
    created_at = db.Column(db.Text)
    updated_at = db.Column(db.Text)
    last_user = db.Column(db.Text)
    last_pc = db.Column(db.Text)
    last_update = db.Column(db.Text)

    def to_dict(self):
        return {c.name: getattr(self, c.name) or "" for c in self.__table__.columns}


# =========================
# 초기화
# =========================
with app.app_context():
    db.create_all()

def now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# API
# =========================
@app.get("/ping")
def ping():
    return "pong"

@app.get("/orders")
def list_orders():
    q = (request.args.get("q") or "").strip()
    query = PurchaseOrder.query

    if q:
        like = f"%{q}%"
        query = query.filter(
            PurchaseOrder.vendor.ilike(like) |
            PurchaseOrder.po_number.ilike(like) |
            PurchaseOrder.equip_name.ilike(like) |
            PurchaseOrder.remark.ilike(like)
        )

    rows = query.order_by(PurchaseOrder.id.desc()).all()
    return jsonify([r.to_dict() for r in rows])

@app.get("/orders/<int:oid>")
def get_order(oid):
    r = PurchaseOrder.query.get_or_404(oid)
    return jsonify(r.to_dict())

@app.post("/orders")
def create_order():
    data = request.get_json(force=True)
    ts = now_ts()

    r = PurchaseOrder(**data)
    r.created_at = ts
    r.updated_at = ts
    r.row_version = 1

    db.session.add(r)
    db.session.commit()
    return jsonify(r.to_dict()), 201

@app.put("/orders/<int:oid>")
def update_order(oid):
    r = PurchaseOrder.query.get_or_404(oid)
    data = request.get_json(force=True)

    if int(data.get("row_version", 1)) != r.row_version:
        return jsonify({"error": "conflict", "db_version": r.row_version}), 409

    for k, v in data.items():
        if hasattr(r, k):
            setattr(r, k, v)

    r.row_version += 1
    r.updated_at = now_ts()
    db.session.commit()
    return jsonify(r.to_dict())

@app.delete("/orders/<int:oid>")
def delete_order(oid):
    r = PurchaseOrder.query.get_or_404(oid)
    db.session.delete(r)
    db.session.commit()
    return jsonify({"ok": True})

# =========================
# 파일 업로드 / 서빙 (기존 로직 유지)
# =========================
FILE_FIELDS = {"invoice_file", "workconfirm_file", "inspect_file", "extra_pdf_file"}

@app.post("/orders/<int:oid>/files")
def upload_files(oid):
    r = PurchaseOrder.query.get_or_404(oid)

    for field, fs in request.files.items():
        if field not in FILE_FIELDS:
            continue
        name = secure_filename(fs.filename)
        save_dir = UPLOAD_ROOT / str(oid) / field
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / name
        fs.save(path)

        setattr(r, field, f"/files/{oid}/{field}/{name}")

    r.row_version += 1
    r.updated_at = now_ts()
    db.session.commit()
    return jsonify(r.to_dict())

@app.get("/files/<path:subpath>")
def serve_files(subpath):
    full = (UPLOAD_ROOT / subpath).resolve()
    if not full.exists():
        abort(404)
    return send_from_directory(full.parent, full.name)
