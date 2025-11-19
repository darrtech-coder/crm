from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from . import leads_bp
from ..extensions import db
from .models import Lead
from ..notifications.utils import create_notification
from ..utils.rbac import role_required
from datetime import datetime
from ..models import User
# -------------------- [START] Add LeadNote to imports --------------------
from .models import Lead, LeadNote
# -------------------- [END] Add LeadNote to imports --------------------



@leads_bp.route("/")
@login_required
def index():
    if current_user.role in ("ADMIN","SUPER_ADMIN"):
        leads=Lead.query.all()
    elif current_user.role=="MANAGER":
        leads=Lead.query.all()  # simplified: managers see all, refine by teams later
    else:
        # --- [FIX] Agents should see leads they created OR leads assigned to them ---
        leads = Lead.query.filter(
            (Lead.created_by == current_user.id) | 
            (Lead.assigned_to == current_user.id)
        ).all()

    # -------------------- [START] Fetch users for assignment dropdown --------------------
    assignable_users = User.query.filter(User.disabled == False).order_by(User.username).all()
    return render_template("leads/index.html", leads=leads, assignable_users=assignable_users)
    # -------------------- [END] Fetch users for assignment dropdown --------------------



# -------------------- [START] New Route for Lead Details and Notes --------------------
@leads_bp.route("/<int:lead_id>")
@login_required
def detail(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    # Basic permission check
    if current_user.role == "AGENT" and lead.assigned_to != current_user.id and lead.created_by != current_user.id:
        flash("You do not have permission to view this lead.", "danger")
        return redirect(url_for("leads.index"))
    return render_template("leads/detail.html", lead=lead)

@leads_bp.route("/<int:lead_id>/add_note", methods=["POST"])
@login_required
def add_note(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    # Basic permission check
    if current_user.role == "AGENT" and lead.assigned_to != current_user.id and lead.created_by != current_user.id:
        flash("You do not have permission to add a note to this lead.", "danger")
        return redirect(url_for("leads.index"))

    note_text = request.form.get("note")
    if not note_text or not note_text.strip():
        flash("Note cannot be empty.", "warning")
        return redirect(url_for("leads.detail", lead_id=lead_id))

    new_note = LeadNote(
        lead_id=lead.id,
        user_id=current_user.id,
        note=note_text.strip()
    )
    db.session.add(new_note)
    db.session.commit()
    flash("Note added successfully.", "success")
    return redirect(url_for("leads.detail", lead_id=lead_id))
# -------------------- [END] New Route for Lead Details and Notes --------------------





@leads_bp.route("/<int:lead_id>/assign", methods=["POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def assign(lead_id):
    assignee_id = int(request.form["user_id"])
    lead = Lead.query.get_or_404(lead_id)
    
    # Check if assignment has changed before sending notification
    if lead.assigned_to != int(assignee_id):
        lead.assigned_to = assignee_id
        db.session.commit()
        # ✅ Notification logic is already here and correct.
        create_notification(assignee_id, f"You have been assigned a new lead: {lead.name}")
        flash(f"Lead assigned to user ID {assignee_id}", "success")
    else:
        flash("Lead is already assigned to this user.", "info")
        
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


