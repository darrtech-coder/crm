from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from . import leads_bp
from ..extensions import db
from .models import Lead
from ..notifications.utils import create_notification
from ..utils.rbac import role_required

@leads_bp.route("/")
@login_required
def index():
    if current_user.role in ("ADMIN","SUPER_ADMIN"):
        leads=Lead.query.all()
    elif current_user.role=="MANAGER":
        leads=Lead.query.all()  # simplified: managers see all, refine by teams later
    else:
        leads=Lead.query.filter_by(assigned_to=current_user.id).all()
    return render_template("leads/index.html", leads=leads)


@leads_bp.route("/<int:lead_id>/assign", methods=["POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def assign(lead_id):
    assignee_id=request.form["user_id"]
    lead=Lead.query.get_or_404(lead_id)
    lead.assigned_to=assignee_id; db.session.commit()
    create_notification(assignee_id,f"You were assigned lead {lead.name}")
    flash("Assigned","success")
    return redirect(url_for("leads.index"))

@leads_bp.route("/<int:lead_id>/update", methods=["POST"])
@login_required
def update_status(lead_id):
    lead=Lead.query.get_or_404(lead_id)
    if current_user.role=="AGENT" and lead.assigned_to!=current_user.id:
        flash("Denied","danger"); return redirect(url_for("leads.index"))
    status=request.form.get("status")
    lead.status=status; lead.notes=request.form.get("notes", lead.notes)
    db.session.commit(); flash("Updated","success")
    return redirect(url_for("leads.index"))


@leads_bp.route("/create", methods=["GET","POST"])
@login_required
def create():
    if current_user.role != "AGENT":
        flash("Only agents can create leads","danger")
        return redirect(url_for("leads.index"))

    if request.method == "POST":
        name = request.form["name"].strip()
        phone = request.form["phone"].strip()
        email = request.form.get("email")

        if not name or not phone:
            flash("Name and phone are required","danger")
            return redirect(url_for("leads.create"))

        lead = Lead(
            name=name,
            phone=phone,
            email=email,
            created_by=current_user.id,
            status="open",
            review_status="pending"
        )
        db.session.add(lead)
        db.session.commit()
        flash("Lead submitted","success")
        return redirect(url_for("leads.index"))

    return render_template("leads/create.html")


@leads_bp.route("/<int:lead_id>/review", methods=["POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def review(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    review_status = request.form["review_status"]
    workflow_status = request.form.get("status")

    lead.review_status = review_status
    lead.reviewed_by = current_user.id
    lead.reviewed_at = datetime.utcnow()
    if workflow_status:
        lead.status = workflow_status

    db.session.commit()
    flash("Lead reviewed","success")
    return redirect(url_for("leads.index"))


