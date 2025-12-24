import os
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

# =========================
# Flask / DB 설정
# =========================
app = Flask(__name__)
CORS(app)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 환경변수가 비어 있습니다. Render Web Service 환경변수에 DATABASE_URL을 설정하세요.")

# Render Postgres는 보통 SSL 필요
if DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy는 postgresql:// 를 권장
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


def _utcnow():
    return datetime.now(timezone.utc)


class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"

    id = db.Column(db.Integer, primary_key=True)

    # ===== 엑셀(통합설치이력) 레이아웃 기반 컬럼 =====
    no = db.Column(db.Integer)\n    category = db.Column(db.Text)\n    work_date = db.Column(db.Text)\n    self_test = db.Column(db.Boolean, default=False)\n    env_cert = db.Column(db.Boolean, default=False)\n    worker = db.Column(db.Text)\n    material = db.Column(db.Text)\n    zone = db.Column(db.Text)\n    progress_status = db.Column(db.Text)\n    discharge_condition = db.Column(db.Text)\n    region = db.Column(db.Text)\n    division = db.Column(db.Text)\n    line = db.Column(db.Text)\n    floor = db.Column(db.Text)\n    bay = db.Column(db.Text)\n    pillar_no = db.Column(db.Text)\n    manage_no = db.Column(db.Text)\n    process = db.Column(db.Text)\n    equip_name = db.Column(db.Text)\n    controlbox_sn = db.Column(db.Text)\n    maker = db.Column(db.Text)\n    equip_model = db.Column(db.Text)\n    equip_serial = db.Column(db.Text)\n    po_no = db.Column(db.Text)\n    q_code = db.Column(db.Text)\n    sales_owner = db.Column(db.Text)\n    invoice_no = db.Column(db.Text)\n    subcontract_po = db.Column(db.Text)\n    subcontract_settlement = db.Column(db.Text)\n    agent_type = db.Column(db.Text)\n    agent_7kg = db.Column(db.Boolean, default=False)\n    agent_14kg = db.Column(db.Boolean, default=False)\n    agent_25kg = db.Column(db.Boolean, default=False)\n    agent_45kg = db.Column(db.Boolean, default=False)\n    agent_grating = db.Column(db.Boolean, default=False)\n    flame_c20 = db.Column(db.Boolean, default=False)\n    flame_c30 = db.Column(db.Boolean, default=False)\n    temp_normal = db.Column(db.Boolean, default=False)\n    temp_coated = db.Column(db.Boolean, default=False)\n    temp_exproof = db.Column(db.Boolean, default=False)\n    smoke_micra25 = db.Column(db.Boolean, default=False)\n    smoke_vesda_new = db.Column(db.Boolean, default=False)\n    smoke_vesda_old = db.Column(db.Boolean, default=False)\n    smoke_photo = db.Column(db.Boolean, default=False)\n    oxygen_detector = db.Column(db.Boolean, default=False)\n    nozzle_diffuse = db.Column(db.Boolean, default=False)\n    nozzle_direct_coat = db.Column(db.Boolean, default=False)\n    auto_damper_mode = db.Column(db.Text)\n    sus_50a = db.Column(db.Boolean, default=False)\n    sus_100a = db.Column(db.Boolean, default=False)\n    sus_150a = db.Column(db.Boolean, default=False)\n    sus_200a = db.Column(db.Boolean, default=False)\n    sus_250a = db.Column(db.Boolean, default=False)\n    pvc_50a = db.Column(db.Boolean, default=False)\n    pvc_75a = db.Column(db.Boolean, default=False)\n    pvc_100a = db.Column(db.Boolean, default=False)\n    pvc_150a = db.Column(db.Boolean, default=False)\n    pvc_200a = db.Column(db.Boolean, default=False)\n    pvc_250a = db.Column(db.Boolean, default=False)\n    indicator = db.Column(db.Boolean, default=False)\n    beacon = db.Column(db.Boolean, default=False)\n    manual_box_crm = db.Column(db.Boolean, default=False)\n    manual_box_psda = db.Column(db.Boolean, default=False)\n    manual_box_masteco = db.Column(db.Boolean, default=False)\n    remark = db.Column(db.Text)\n
    # ===== 메타 =====
    row_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    def to_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        # datetime 직렬화
        for k in ("created_at", "updated_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        return d


def ensure_schema():
    """
    - 테이블 없으면 생성
    - 이미 있으면, 누락 컬럼만 ALTER TABLE로 추가
    """
    db.create_all()

    # 누락 컬럼 체크(Information Schema)
    expected = [c.name for c in PurchaseOrder.__table__.columns]
    existing = set()
    with db.engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
        """), {"t": PurchaseOrder.__tablename__}).fetchall()
        for r in rows:
            existing.add(r[0])

        # Postgres에 없는 컬럼만 추가
        for col in PurchaseOrder.__table__.columns:
            name = col.name
            if name in existing:
                continue
            # 타입 매핑
            if isinstance(col.type, db.Boolean().type.__class__):
                sql_type = "BOOLEAN"
                default = " DEFAULT FALSE"
            elif isinstance(col.type, db.Integer().type.__class__):
                sql_type = "INTEGER"
                default = ""
                if name == "row_version":
                    default = " DEFAULT 1"
            else:
                sql_type = "TEXT"
                default = ""
            conn.execute(text(f'ALTER TABLE {PurchaseOrder.__tablename__} ADD COLUMN {name} {sql_type}{default}'))

    # sqlite같은 다른 엔진에서는 information_schema가 없을 수 있는데,
    # Render에서는 Postgres 사용 전제이므로 여기까지만 처리합니다.


with app.app_context():
    ensure_schema()

# 허용 컬럼(서버는 이 목록만 저장/수정 허용)
ALLOWED_COLUMNS = {c.name for c in PurchaseOrder.__table__.columns}
ALLOWED_COLUMNS.discard("id")
ALLOWED_COLUMNS.discard("created_at")
ALLOWED_COLUMNS.discard("updated_at")


def _normalize_value(key, val):
    # 빈 문자열 -> None (Postgres 타입 안전)
    if val == "":
        return None

    # boolean 컬럼은 다양한 입력을 받아줌
    bool_cols = {
        "self_test",
        "env_cert",
        "agent_7kg",
        "agent_14kg",
        "agent_25kg",
        "agent_45kg",
        "agent_grating",
        "flame_c20",
        "flame_c30",
        "temp_normal",
        "temp_coated",
        "temp_exproof",
        "smoke_micra25",
        "smoke_vesda_new",
        "smoke_vesda_old",
        "smoke_photo",
        "oxygen_detector",
        "nozzle_diffuse",
        "nozzle_direct_coat",
        "sus_50a",
        "sus_100a",
        "sus_150a",
        "sus_200a",
        "sus_250a",
        "pvc_50a",
        "pvc_75a",
        "pvc_100a",
        "pvc_150a",
        "pvc_200a",
        "pvc_250a",
        "indicator",
        "beacon",
        "manual_box_crm",
        "manual_box_psda",
        "manual_box_masteco",
    }
    if key in bool_cols:
        if isinstance(val, bool):
            return val
        if val is None:
            return False
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            v = val.strip().lower()
            return v in ("1", "true", "t", "y", "yes", "on", "✅", "v")
        return False

    return val


@app.get("/ping")
def ping():
    return "pong"


@app.get("/orders")
def list_orders():
    q = (request.args.get("q") or "").strip()

    query = PurchaseOrder.query
    if q:
        # 자주 쓰는 텍스트 필드 위주로 검색
        like = f"%{q}%"
        query = query.filter(
            (PurchaseOrder.po_no.ilike(like)) |
            (PurchaseOrder.q_code.ilike(like)) |
            (PurchaseOrder.equip_name.ilike(like)) |
            (PurchaseOrder.region.ilike(like)) |
            (PurchaseOrder.division.ilike(like)) |
            (PurchaseOrder.manage_no.ilike(like)) |
            (PurchaseOrder.invoice_no.ilike(like))
        )

    rows = query.order_by(PurchaseOrder.id.desc()).limit(500).all()
    return jsonify([r.to_dict() for r in rows])


@app.get("/orders/<int:order_id>")
def get_order(order_id: int):
    row = PurchaseOrder.query.get_or_404(order_id)
    return jsonify(row.to_dict())


@app.post("/orders")
def create_order():
    data = request.get_json(silent=True) or {}

    clean = {}
    for k, v in data.items():
        if k in ALLOWED_COLUMNS:
            clean[k] = _normalize_value(k, v)

    # created_at/updated_at은 자동
    row = PurchaseOrder(**clean)
    db.session.add(row)
    db.session.commit()
    return jsonify(row.to_dict()), 201


@app.put("/orders/<int:order_id>")
def update_order(order_id: int):
    data = request.get_json(silent=True) or {}

    row = PurchaseOrder.query.get_or_404(order_id)

    # optimistic lock
    client_ver = data.get("row_version")
    if client_ver is None:
        return jsonify({"error": "row_version is required"}), 400

    try:
        client_ver = int(client_ver)
    except Exception:
        return jsonify({"error": "row_version must be int"}), 400

    if row.row_version != client_ver:
        return jsonify({"error": "version_conflict", "db_version": row.row_version}), 409

    for k, v in data.items():
        if k in ALLOWED_COLUMNS and k != "row_version":
            setattr(row, k, _normalize_value(k, v))

    row.row_version = row.row_version + 1
    db.session.commit()
    return jsonify(row.to_dict())


@app.delete("/orders/<int:order_id>")
def delete_order(order_id: int):
    row = PurchaseOrder.query.get_or_404(order_id)
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})
