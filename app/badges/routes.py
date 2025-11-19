import os
from flask import render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required
from werkzeug.utils import secure_filename
from . import badges_bp
from .models import Badge, UserBadge
from ..models import User
from ..extensions import db
from ..utils.rbac import role_required

# REMOVED: Do not define this at the module level
# UPLOAD_FOLDER = os.path.join(current_app.root_path, 'static', 'badges')

@badges_bp.route("/admin")
@login_required
@role_required("ADMIN", "SUPER_ADMIN")
def admin_index():
    badges = Badge.query.order_by(Badge.name).all()
    users = User.query.order_by(User.username).all()
    return render_template("badges/admin_badges.html", badges=badges, users=users)

@badges_bp.route("/admin/create", methods=["POST"])
@login_required
@role_required("ADMIN", "SUPER_ADMIN")
def create_badge():
    # ADDED: Define UPLOAD_FOLDER inside the function context
    UPLOAD_FOLDER = os.path.join(current_app.root_path, 'static', 'badges')

    name = request.form.get("name")
    description = request.form.get("description")
    file = request.files.get("image_file")

    if not name or not file or not file.filename:
        flash("Badge name and image file are required.", "danger")
        return redirect(url_for("badges.admin_index"))

    filename = secure_filename(file.filename)
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    
    badge = Badge(name=name, description=description, image_file=filename)
    db.session.add(badge)
    db.session.commit()
    
    flash(f"Badge '{name}' created successfully.", "success")
    return redirect(url_for("badges.admin_index"))

@badges_bp.route("/admin/award", methods=["POST"])
@login_required
@role_required("ADMIN", "SUPER_ADMIN")
def award_badge():
    user_id = request.form.get("user_id")
    badge_id = request.form.get("badge_id")
    
    if not user_id or not badge_id:
        flash("User and Badge must be selected.", "danger")
        return redirect(url_for("badges.admin_index"))

    # Check if user already has this badge
    existing = UserBadge.query.filter_by(user_id=user_id, badge_id=badge_id).first()
    if existing:
        flash("User already has this badge.", "info")
        return redirect(url_for("badges.admin_index"))
        
    user_badge = UserBadge(user_id=user_id, badge_id=badge_id)
    db.session.add(user_badge)
    db.session.commit()
    
    flash("Badge awarded successfully!", "success")
    return redirect(url_for("badges.admin_index"))