from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from datetime import datetime
from ..extensions import db
from . import profile_bp

@profile_bp.route("/", methods=["GET","POST"])
@login_required
def edit_profile():
    if request.method=="POST":
        current_user.name=request.form.get("name", current_user.name)
        current_user.email=request.form.get("email", current_user.email)

        dob_str=request.form.get("dob")
        if dob_str:
            try:
                current_user.dob=datetime.strptime(dob_str,"%Y-%m-%d").date()
            except Exception:
                flash("Invalid DOB format","danger")

        gender=request.form.get("gender")
        if gender: current_user.gender=gender

        new_pw=request.form.get("password")
        if new_pw:
            current_user.password=generate_password_hash(new_pw)

        # ✅ Theme update
        theme = request.form.get("theme")
        if theme in ("light", "dark"):
            current_user.theme = theme

        db.session.commit()
        flash("Profile updated","success")
        return redirect(url_for("profile.edit_profile"))
    return render_template("profile/edit.html", user=current_user)
