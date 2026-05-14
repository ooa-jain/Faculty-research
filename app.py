import os
import json
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, flash
)
from pymongo import MongoClient, DESCENDING
from bson import ObjectId
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fallback-secret-key")

# ── MongoDB ──────────────────────────────────────────────────────────────────
client = MongoClient(os.getenv("MONGO_URI"))
db = client.get_default_database()
responses_col = db["survey_responses"]

# ── Helpers ──────────────────────────────────────────────────────────────────
class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)

app.json_encoder = JSONEncoder


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


# ── Survey Routes ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("survey.html")


@app.route("/api/submit", methods=["POST"])
def submit_survey():
    payload = request.get_json(force=True)
    if not payload:
        return jsonify({"ok": False, "error": "Empty payload"}), 400

    # Basic consent gate
    consent = payload.get("consent", {})
    if not consent.get("agreed"):
        return jsonify({"ok": False, "error": "Consent not given"}), 400

    doc = {
        **payload,
        "submitted_at": datetime.now(timezone.utc),
        "ip": request.remote_addr,
    }
    result = responses_col.insert_one(doc)
    return jsonify({"ok": True, "id": str(result.inserted_id)}), 201


# ── Admin Routes ──────────────────────────────────────────────────────────────
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if (username == os.getenv("ADMIN_USERNAME", "admin") and
                password == os.getenv("ADMIN_PASSWORD", "admin123")):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid credentials", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    total = responses_col.count_documents({})
    consented = responses_col.count_documents({"consent.agreed": True})

    # Aggregation stats
    pipeline_designation = [
        {"$group": {"_id": "$identity.designation", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    designation_data = list(responses_col.aggregate(pipeline_designation))

    pipeline_domain = [
        {"$group": {"_id": "$research.domain", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    domain_data = list(responses_col.aggregate(pipeline_domain))

    pipeline_ai = [
        {"$unwind": "$aitools.toolsUsed"},
        {"$group": {"_id": "$aitools.toolsUsed", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    ai_tools_data = list(responses_col.aggregate(pipeline_ai))

    pipeline_comfort = [
        {"$group": {"_id": "$aitools.overallComfort", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}}
    ]
    comfort_data = list(responses_col.aggregate(pipeline_comfort))

    # Recent submissions
    recent = list(responses_col.find(
        {}, {"identity": 1, "submitted_at": 1, "research.domain": 1,
             "aitools.overallComfort": 1, "consent.anonymous": 1}
    ).sort("submitted_at", DESCENDING).limit(10))

    return render_template(
        "admin_dashboard.html",
        total=total,
        consented=consented,
        designation_data=designation_data,
        domain_data=domain_data,
        ai_tools_data=ai_tools_data,
        comfort_data=comfort_data,
        recent=recent,
    )


@app.route("/admin/responses")
@admin_required
def admin_responses():
    page = int(request.args.get("page", 1))
    per_page = 20
    skip = (page - 1) * per_page
    total = responses_col.count_documents({})
    docs = list(responses_col.find({}).sort(
        "submitted_at", DESCENDING).skip(skip).limit(per_page))
    pages = (total + per_page - 1) // per_page
    return render_template(
        "admin_responses.html",
        docs=docs, page=page, pages=pages, total=total
    )


@app.route("/admin/response/<response_id>")
@admin_required
def admin_response_detail(response_id):
    doc = responses_col.find_one({"_id": ObjectId(response_id)})
    if not doc:
        flash("Response not found", "error")
        return redirect(url_for("admin_responses"))
    return render_template("admin_response_detail.html", doc=doc)


@app.route("/admin/export/json")
@admin_required
def export_json():
    docs = list(responses_col.find({}))
    for d in docs:
        d["_id"] = str(d["_id"])
        if "submitted_at" in d:
            d["submitted_at"] = d["submitted_at"].isoformat()
    response = app.response_class(
        json.dumps(docs, indent=2, ensure_ascii=False),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=fdp_responses.json"}
    )
    return response


@app.route("/admin/delete/<response_id>", methods=["POST"])
@admin_required
def admin_delete(response_id):
    responses_col.delete_one({"_id": ObjectId(response_id)})
    flash("Response deleted.", "success")
    return redirect(url_for("admin_responses"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
