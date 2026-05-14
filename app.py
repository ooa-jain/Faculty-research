import os
import json
import io
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session, flash, send_file
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

    # Recent submissions — always show name (admin view)
    recent = list(responses_col.find(
        {}, {"identity": 1, "submitted_at": 1, "research.domain": 1,
             "aitools.overallComfort": 1, "consent": 1}
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


@app.route("/admin/export/excel")
@admin_required
def export_excel():
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return "openpyxl not installed. Run: pip install openpyxl", 500

    docs = list(responses_col.find({}).sort("submitted_at", DESCENDING))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "FDP Responses"

    # Header style
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="00B4A6")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    headers = [
        "#", "Submitted At", "Name", "Email", "Institution", "Department",
        "Designation", "Years Exp", "ORCID",
        "Research Domain", "Sub-Domain", "Keywords", "Paradigm", "Focus",
        "Pub Total", "Indexed", "h-Index", "Citations", "Patents",
        "AI Comfort", "AI Tools Used", "AI Concerns",
        "Innovation Project", "Innovation Stage",
        "Integration Level",
        "One Year Goal", "Commitment Action", "Additional Thoughts",
        "Consented"
    ]

    ws.row_dimensions[1].height = 30
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        ws.column_dimensions[cell.column_letter].width = max(14, len(h) + 2)

    def flat(val):
        if isinstance(val, list):
            return ", ".join(str(v) for v in val)
        if val is None:
            return ""
        return str(val)

    for i, doc in enumerate(docs, 1):
        ident = doc.get("identity", {})
        res = doc.get("research", {})
        pub = doc.get("publication", {})
        ai = doc.get("aitools", {})
        inno = doc.get("innovation", {})
        integ = doc.get("integration", {})
        asp = doc.get("aspirations", {})
        con = doc.get("consent", {})
        submitted = doc.get("submitted_at", "")
        if hasattr(submitted, "strftime"):
            submitted = submitted.strftime("%d %b %Y %H:%M")

        row = [
            i,
            submitted,
            flat(ident.get("name")),
            flat(ident.get("email")),
            flat(ident.get("institution")),
            flat(ident.get("department")),
            flat(ident.get("designation")),
            flat(ident.get("yearsExp")),
            flat(ident.get("orcid")),
            flat(res.get("domain")),
            flat(res.get("subDomain")),
            flat(res.get("keywords")),
            flat(res.get("paradigm")),
            flat(res.get("focus")),
            flat(pub.get("total")),
            flat(pub.get("indexed")),
            flat(pub.get("hIndex")),
            flat(pub.get("citations")),
            flat(pub.get("patents")),
            flat(ai.get("overallComfort")),
            flat(ai.get("toolsUsed")),
            flat(ai.get("concerns")),
            flat(inno.get("hasProject")),
            flat(inno.get("stage")),
            flat(integ.get("integrationLevel")),
            flat(asp.get("oneYearGoal")),
            flat(asp.get("commitmentAction")),
            flat(asp.get("additionalThoughts")),
            "Yes" if con.get("agreed") else "No",
        ]
        for col_idx, val in enumerate(row, 1):
            cell = ws.cell(row=i + 1, column=col_idx, value=val)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[i + 1].height = 18

    # Freeze header
    ws.freeze_panes = "A2"

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="fdp_responses.xlsx"
    )


@app.route("/admin/delete/<response_id>", methods=["POST"])
@admin_required
def admin_delete(response_id):
    responses_col.delete_one({"_id": ObjectId(response_id)})
    flash("Response deleted.", "success")
    return redirect(url_for("admin_responses"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
