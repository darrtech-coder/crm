import os
# from ..activity.models import LibrarySession
from flask import (
    render_template, request, redirect,
    url_for, flash, send_from_directory, current_app, jsonify
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from . import library_bp
from .models import (
    LibraryItem, LibraryAttachment, FAQ, QuizQuestion, QuizOption,
    QuizAttempt, LibraryView, LibraryRating, LibraryBias,
    BiasLog, LibraryCategory, TrendingItem, LibraryAccess, LibraryProgress
)
from ..extensions import db
from .utils import extract_text
from .recommend import get_recommendations
from ..utils.rbac import role_required
from sqlalchemy import func

# -------------------- [START] Add required imports --------------------
from sqlalchemy import func, or_
from ..models import User
from ..teams.models import Team
from datetime import datetime
from .models import LibraryPrerequisite, LibraryView
# -------------------- [END] Add required imports --------------------

# folders & allowed extensions
ALLOWED_EXTENSIONS = {
    "txt","pdf","png","jpg","jpeg","gif","webp",
    "doc","docx","xls","xlsx",
    "ppt","pptx",
    "mp3","wav","ogg",
    "mp4","avi","mov","mkv","webm"
}

def allowed_file(fname: str):
    return "." in fname and fname.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------- Library Home ----------------
@library_bp.route("/")
@login_required
def index():
    q = request.args.get("q", "")
    category_param = request.args.get("category")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 12, type=int)

    # Base query: exclude archived + unlisted
    query = LibraryItem.query.filter_by(archived=False)

    # 🔒 Hide restricted items from AGENTS
    if current_user.role == "AGENT":
        query = query.filter(LibraryItem.manager_only == False)

        allowed_ids = []
        from .models import LibraryAccess
        for acc in LibraryAccess.query.filter_by(user_id=current_user.id).all():
            allowed_ids.append(acc.item_id)
        team_ids = [tm.team_id for tm in current_user.team_memberships]
        for acc in LibraryAccess.query.filter(LibraryAccess.team_id.in_(team_ids)).all():
            allowed_ids.append(acc.item_id)

        if allowed_ids:
            query = query.filter(
                (~LibraryItem.restricted_access.any()) |
                (LibraryItem.id.in_(allowed_ids))
            )
        else:
            query = query.filter(~LibraryItem.restricted_access.any())

    # --- Handle search ---
    if q:
        like = f"%{q}%"
        query = query.filter(
            (LibraryItem.title.ilike(like)) |
            (LibraryItem.description.ilike(like)) |
            (LibraryItem.keywords.ilike(like)) |
            (LibraryItem.filename.ilike(like)) |
            (LibraryItem.text_content.ilike(like))
        )

    # --- Handle category filter ---
    current_cat = None
    if category_param:
        if category_param == "none":
            query = query.filter(LibraryItem.category_id == None)
            current_cat = "none"
        else:
            try:
                category_id = int(category_param)
                query = query.filter_by(category_id=category_id)
                current_cat = category_id
            except ValueError:
                pass
    else:
        current_cat = "all"

    # --- Paginate ---
    pagination = query.order_by(LibraryItem.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    items = pagination.items

    categories = LibraryCategory.query.order_by(LibraryCategory.name.asc()).all()
    suggested = get_recommendations(current_user)[:5]

    # Exclude unlisted from system trending
    system_trending = (
        db.session.query(LibraryItem, func.count(LibraryView.id).label("view_count"))
        .join(LibraryView, LibraryView.item_id == LibraryItem.id)
        .filter(LibraryItem.unlisted == False)
        .group_by(LibraryItem.id)
        .order_by(func.count(LibraryView.id).desc())
        .limit(5).all()
    )

    team_ids = [tm.team_id for tm in current_user.team_memberships]
    manual_trending_items = [
        t.item
        for t in TrendingItem.query.filter(
            (TrendingItem.team_id == None) | (TrendingItem.team_id.in_(team_ids))
        ).all()
    ]

    return render_template(
        "library/index.html",
        items=items,
        pagination=pagination,
        q=q,
        categories=categories,
        current_cat=current_cat,
        per_page=per_page,
        suggested=suggested,
        system_trending=system_trending,
        manual_trending=manual_trending_items,
    )






# ---------------- Category Management ----------------
@library_bp.route("/categories", methods=["GET","POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def manage_categories():
    if request.method == "POST":
        name = request.form.get("name")
        if name:
            db.session.add(LibraryCategory(name=name))
            db.session.commit()
            flash("Category added","success")
        return redirect(url_for("library.manage_categories"))
    return render_template("library/categories.html", categories=LibraryCategory.query.all())


@library_bp.route("/categories/<int:cat_id>/edit", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def edit_category(cat_id):
    cat = LibraryCategory.query.get_or_404(cat_id)
    new_name = request.form.get("name","").strip()
    if new_name:
        cat.name = new_name
        db.session.commit()
        flash("Category updated","success")
    else:
        flash("Name cannot be empty","danger")
    return redirect(url_for("library.manage_categories"))

@library_bp.route("/categories/<int:cat_id>/delete", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def delete_category(cat_id):
    cat = LibraryCategory.query.get_or_404(cat_id)
    # Reassign items to Uncategorized
    LibraryItem.query.filter_by(category_id=cat.id).update({LibraryItem.category_id: None})
    db.session.delete(cat)
    db.session.commit()
    flash("Category deleted. Items moved to Uncategorized.","warning")
    return redirect(url_for("library.manage_categories"))



# -------------------- [START] New AJAX route for user search --------------------
@library_bp.route("/search_users")
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def search_users():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    like_query = f"%{q}%"
    users = User.query.filter(
        or_(
            User.username.ilike(like_query),
            User.email.ilike(like_query),
            User.name.ilike(like_query)
        )
    ).limit(10).all()

    results = [
        {"id": user.id, "text": f"{user.name or user.username} ({user.email})"}
        for user in users
    ]
    return jsonify(results)
# -------------------- [END] New AJAX route for user search --------------------



# ---------------- Upload ----------------
from ..utils.thumbnails import auto_generate_thumbnail, generate_video_thumbnail, generate_image_thumbnail, generate_pdf_thumbnail

@library_bp.route("/upload", methods=["GET","POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def upload():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        desc = request.form.get("description")
        keywords = request.form.get("keywords")
        category_id = request.form.get("category_id") or None
        unlisted_flag = bool(request.form.get("unlisted"))

        upload_dir = os.path.join(current_app.root_path, "..", "uploads", "library")
        os.makedirs(upload_dir, exist_ok=True)

        main_file = request.files.get("file")
        if not main_file or not allowed_file(main_file.filename):
            flash("Invalid or missing file","danger")
            return redirect(url_for("library.upload"))

        # Save main file
        filename = secure_filename(main_file.filename)
        filepath = os.path.join(upload_dir, filename)
        main_file.save(filepath)

        # ✅ Auto-title if empty
        if not title:
            base, _ = os.path.splitext(filename)
            title = base

        thumbnail_name = None
        thumb_base = os.path.splitext(filename)[0] + "_thumb.jpg"
        thumb_path = os.path.join(upload_dir, thumb_base)

        # ✅ Auto-generate thumbnails depending on mime
        if "video" in main_file.mimetype:
            if generate_video_thumbnail(filepath, thumb_path):
                thumbnail_name = thumb_base
        elif "image" in main_file.mimetype:
            if generate_image_thumbnail(filepath, thumb_path):
                thumbnail_name = thumb_base
        elif "pdf" in main_file.mimetype:
            if generate_pdf_thumbnail(filepath, thumb_path):
                thumbnail_name = thumb_base

        # ✅ If user provided custom thumbnail → override
        thumb_file = request.files.get("thumbnail")
        if thumb_file and allowed_file(thumb_file.filename):
            t_name = secure_filename(thumb_file.filename)
            t_path = os.path.join(upload_dir, t_name)
            thumb_file.save(t_path)
            thumbnail_name = t_name

        # Create library item
        item = LibraryItem(
            title=title,
            description=desc,
            keywords=keywords,
            filename=filename,
            mime=main_file.mimetype,
            size=os.path.getsize(filepath),
            creator_id=current_user.id,
            category_id=int(category_id) if category_id else None,
            thumbnail=thumbnail_name
        )
        # Optional: set unlisted if column exists
        if hasattr(LibraryItem, "unlisted"):
            item.unlisted = unlisted_flag

        # ✅ Now add restricted flags
        item.manager_only = bool(request.form.get("manager_only"))

        # Extract searchable text (for docs)
        if main_file.mimetype.startswith("text") or \
           "pdf" in main_file.mimetype or \
           "word" in main_file.mimetype:
            item.text_content = extract_text(filepath, main_file.mimetype)

        db.session.add(item)
        db.session.flush()

        # Handle attachments
        for attach in request.files.getlist("attachments"):
            if attach and allowed_file(attach.filename):
                a_name = secure_filename(attach.filename)
                a_path = os.path.join(upload_dir, a_name)
                attach.save(a_path)
                db.session.add(LibraryAttachment(
                    item_id=item.id,
                    filename=a_name,
                    mime=attach.mimetype,
                    size=os.path.getsize(a_path)
                ))

        # 🔥 Restrict access
        team_ids = request.form.getlist("team_ids")

        # Parse specific users (comma-separated)
        user_ids_str = request.form.get("user_ids", "")
        user_ids = [int(uid) for uid in user_ids_str.split(',') if uid.strip().isdigit()]

        # Clear existing user/team specific rules before adding new ones (noop for new items)
        LibraryAccess.query.filter_by(item_id=item.id).delete()

        for tid in team_ids:
            if tid:
                db.session.add(LibraryAccess(item_id=item.id, team_id=int(tid)))
        for uid in user_ids:
            db.session.add(LibraryAccess(item_id=item.id, user_id=uid))

        # Parse comma-separated prereq IDs from form
        prereq_str = request.form.get("prereq_item_ids", "")
        prereq_ids = [int(x) for x in prereq_str.split(",") if x.strip().isdigit()]
        for pid in prereq_ids:
            if pid != item.id:
                db.session.add(LibraryPrerequisite(item_id=item.id, prereq_item_id=pid))

        db.session.commit()

        # Notify all Agents (or everyone allowed)
        from ..notifications.utils import notify_role
        notify_role("AGENT", f"📘 New library item added: {title}")

        flash("Item uploaded successfully","success")
        return redirect(url_for("library.index", item_id=item.id))

    # GET — pass a stub `item` so upload template can safely reference item.* fields
    stub_item = {"unlisted": False, "manager_only": False, "restricted_access": []}
    return render_template(
        "library/upload.html",
        categories=LibraryCategory.query.all(),
        teams=Team.query.all(),
        item=stub_item
    )


# ---------------- View Item ----------------
@library_bp.route("/item/<int:item_id>")
@login_required
def view_item(item_id):
    item = LibraryItem.query.get_or_404(item_id)

    # 🚫 Manager-only restriction
    if item.manager_only and current_user.role == "AGENT":
        flash("Manager-only content", "danger")
        return redirect(url_for("library.index"))

    # 🚫 Access restrictions (teams / agents only)
    if item.restricted_access:
        allowed = False
        # Direct assignment
        if any(acc.user_id == current_user.id for acc in item.restricted_access):
            allowed = True
        # Team assignment
        team_ids = [tm.team_id for tm in current_user.team_memberships]
        if any(acc.team_id in team_ids for acc in item.restricted_access if acc.team_id):
            allowed = True
        # Admins bypass restrictions
        if not allowed and current_user.role not in ("ADMIN", "SUPER_ADMIN"):
            flash("Access restricted to certain users/teams", "danger")
            return redirect(url_for("library.index"))

    # Enforce prerequisite if defined
    pr = LibraryPrerequisite.query.filter_by(item_id=item.id).first()
    if pr:
        # allow if user has a LibraryView record of the prerequisite item
        has_pre = LibraryView.query.filter_by(user_id=current_user.id, item_id=pr.prereq_item_id).first()
        if not has_pre and current_user.role not in ("ADMIN","SUPER_ADMIN"):
            flash("You must complete the prerequisite before viewing this item.", "warning")
            return redirect(url_for("library.view_item", item_id=pr.prereq_item_id))



    # ✅ Record view event
    db.session.add(LibraryView(item_id=item.id, user_id=current_user.id))
    db.session.commit()

    from ..teams.models import Team

    # Precompute dates & scores for charts
    dates = [a.created_at.strftime("%Y-%m-%d") for a in item.quiz_attempts]
    total_q = len(item.quiz_questions) if item.quiz_questions else 0
    scores_percent = [(a.score / total_q * 100) if total_q > 0 else 0 for a in item.quiz_attempts]

    # --- [NEW] Fetch the last saved position for the user ---
    progress = LibraryProgress.query.filter_by(user_id=current_user.id, item_id=item.id).first()
    last_pos = progress.position if progress else 0

    return render_template(
        "library/item.html",
        item=item,
        all_teams=Team.query.all(),
        dates=dates,
        scores_percent=scores_percent,   # passed to template
        last_pos=last_pos                  # <-- add this
    )



# ---------------- Downloads ----------------
@library_bp.route("/item/<int:item_id>/download")
@login_required
def download_item(item_id):
    item = LibraryItem.query.get_or_404(item_id)
    upload_dir = os.path.join(current_app.root_path, "..", "uploads", "library")

    # ✅ Serve thumbnail if requested
    if request.args.get("thumb") and item.thumbnail:
        return send_from_directory(upload_dir, item.thumbnail)

    return send_from_directory(upload_dir, item.filename, as_attachment=False)

@library_bp.route("/attachment/<int:attach_id>/view")
@login_required
def download_attachment(attach_id):
    attach = LibraryAttachment.query.get_or_404(attach_id)
    upload_dir = os.path.join(current_app.root_path, "..", "uploads", "library")
    return send_from_directory(upload_dir, attach.filename, as_attachment=False)


# ---------------- Feedback ----------------
@library_bp.route("/item/<int:item_id>/feedback", methods=["POST"])
@login_required
def feedback(item_id):
    r = LibraryRating(
        item_id=item_id, user_id=current_user.id,
        easy=int(request.form["easy"]),
        complete=int(request.form["complete"]),
        overall=int(request.form["overall"]),
        comment=request.form.get("comment")
    )
    db.session.add(r); db.session.commit()
    flash("Feedback submitted","success")
    return redirect(url_for("library.view_item", item_id=item_id))


# ---------------- FAQs ----------------
@library_bp.route("/item/<int:item_id>/add_faq", methods=["POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def add_faq(item_id):
    db.session.add(FAQ(item_id=item_id,
                       question=request.form["question"],
                       answer=request.form["answer"]))
    db.session.commit()
    flash("FAQ added","success")
    return redirect(url_for("library.view_item", item_id=item_id))


# ---------------- Quiz ----------------
@library_bp.route("/item/<int:item_id>/quiz", methods=["POST"])
@login_required
def quiz_submit(item_id):
    score=0; questions=QuizQuestion.query.filter_by(item_id=item_id).all()
    for q in questions:
        selected = request.form.get(f"q{q.id}")
        if selected and QuizOption.query.get(int(selected)).is_correct:
            score += 1
    db.session.add(QuizAttempt(user_id=current_user.id,item_id=item_id,score=score))
    db.session.commit()
    flash(f"Score {score}/{len(questions)}","info")
    return redirect(url_for("library.view_item", item_id=item_id))


# ---------------- Add Quiz Question ----------------
@library_bp.route("/item/<int:item_id>/quiz/new", methods=["GET","POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def add_quiz_question(item_id):
    if request.method == "POST":
        qtext = request.form.get("question")
        question = QuizQuestion(item_id=item_id, question=qtext)
        db.session.add(question); db.session.flush()  # get id

        # loop options
        options = request.form.getlist("option")
        marks   = request.form.getlist("is_correct")
        for idx, opt_text in enumerate(options):
            if opt_text.strip():
                db.session.add(QuizOption(
                    question_id=question.id,
                    text=opt_text.strip(),
                    is_correct=("on" == marks[idx]) if idx < len(marks) else False
                ))
        db.session.commit()
        flash("Question added","success")
        return redirect(url_for("library.view_item", item_id=item_id))

    return render_template("library/add_quiz.html", item_id=item_id)


# ---------------- Recommendations ----------------
@library_bp.route("/recommended")
@login_required
def recommended():
    return render_template("library/recommended.html", ranked=get_recommendations(current_user))


# ---------------- Bias & Trending ----------------
@library_bp.route("/item/<int:item_id>/set_bias", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def set_bias(item_id):
    item=LibraryItem.query.get_or_404(item_id)
    item.bias_weight=float(request.form.get("bias",0)); db.session.commit()
    db.session.add(BiasLog(item_id=item.id, weight=item.bias_weight, applied_by=current_user.id))
    db.session.commit(); flash("Bias updated","success")
    return redirect(url_for("library.view_item", item_id=item_id))

@library_bp.route("/item/<int:item_id>/set_team_bias", methods=["POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def set_team_bias(item_id):
    team_id=int(request.form["team_id"]); weight=float(request.form["bias"])
    db.session.add(LibraryBias(item_id=item_id, team_id=team_id, weight=weight))
    db.session.add(BiasLog(item_id=item_id, team_id=team_id, weight=weight, applied_by=current_user.id))
    db.session.commit(); flash("Team bias updated","success")
    return redirect(url_for("library.view_item", item_id=item_id))

@library_bp.route("/item/<int:item_id>/set_user_bias", methods=["POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def set_user_bias(item_id):
    uid=int(request.form["user_id"]); weight=float(request.form["bias"])
    db.session.add(LibraryBias(item_id=item_id, user_id=uid, weight=weight))
    db.session.add(BiasLog(item_id=item_id, user_id=uid, weight=weight, applied_by=current_user.id))
    db.session.commit(); flash("User bias updated","success")
    return redirect(url_for("library.view_item", item_id=item_id))

@library_bp.route("/item/<int:item_id>/mark_trending", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def mark_trending(item_id):
    # only mark if not already trending
    existing = TrendingItem.query.filter_by(item_id=item_id).first()
    if not existing:
        team_id = request.form.get("team_id") or None
        db.session.add(TrendingItem(item_id=item_id, team_id=team_id))
        db.session.commit()
        flash("Item marked as highlighted by Admin", "success")
    else:
        flash("Already highlighted", "info")
    return redirect(url_for("library.view_item", item_id=item_id))


# ---------------- Edit Item ----------------
@library_bp.route("/item/<int:item_id>/edit", methods=["GET","POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def edit_item(item_id):
    item = LibraryItem.query.get_or_404(item_id)
    upload_dir = os.path.join(current_app.root_path, "..", "uploads", "library")
    os.makedirs(upload_dir, exist_ok=True)

    if request.method == "POST":
        item.title = request.form["title"]
        item.description = request.form.get("description")
        item.keywords = request.form.get("keywords")
        item.category_id = int(request.form.get("category_id")) if request.form.get("category_id") else None
        item.manager_only = bool(request.form.get("manager_only"))

        # replace main file if new one uploaded
        new_file = request.files.get("file")
        if new_file and allowed_file(new_file.filename):
            filename = secure_filename(new_file.filename)
            new_path = os.path.join(upload_dir, filename)
            new_file.save(new_path)
            item.filename = filename
            item.mime = new_file.mimetype
            item.size = os.path.getsize(new_path)

        # replace thumbnail if new uploaded
        thumb_file = request.files.get("thumbnail")
        if thumb_file and allowed_file(thumb_file.filename):
            t_name = secure_filename(thumb_file.filename)
            t_path = os.path.join(upload_dir, t_name)
            thumb_file.save(t_path)
            item.thumbnail = t_name

        # add new attachments
        for attach in request.files.getlist("attachments"):
            if attach and allowed_file(attach.filename):
                a_name = secure_filename(attach.filename)
                a_path = os.path.join(upload_dir, a_name)
                attach.save(a_path)
                db.session.add(LibraryAttachment(
                    item_id=item.id, filename=a_name,
                    mime=attach.mimetype, size=os.path.getsize(a_path)
                ))

        # 🔥 Restrict access
        team_ids = request.form.getlist("team_ids")
        # -------------------- [START] Update user_ids handling --------------------
        user_ids_str = request.form.get("user_ids", "")
        user_ids = [int(uid) for uid in user_ids_str.split(',') if uid.strip().isdigit()]

        # Clear existing user/team specific rules before adding new ones
        LibraryAccess.query.filter_by(item_id=item.id).delete()
        # -------------------- [END] Update user_ids handling --------------------
        for tid in team_ids:
            if tid:
                db.session.add(LibraryAccess(item_id=item.id, team_id=int(tid)))
        # -------------------- [START] Update user_ids loop --------------------
        for uid in user_ids:
            db.session.add(LibraryAccess(item_id=item.id, user_id=uid))
        # -------------------- [END] Update user_ids loop --------------------


        prereq_str = request.form.get("prereq_item_ids", "")
        prereq_ids = [int(x) for x in prereq_str.split(",") if x.strip().isdigit()]
        # Clear existing
        LibraryPrerequisite.query.filter_by(item_id=item.id).delete()
        # Add new
        for pid in prereq_ids:
            if pid != item.id:
                db.session.add(LibraryPrerequisite(item_id=item.id, prereq_item_id=pid))



        db.session.commit()
        flash("Item updated", "success")
        return redirect(url_for("library.view_item", item_id=item.id))

    categories = LibraryCategory.query.all()
    return render_template("library/edit_item.html", item=item, categories=categories, teams=Team.query.all())


# ---------------- Archive/Delete Item ----------------
@library_bp.route("/item/<int:item_id>/archive", methods=["POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def archive_item(item_id):
    item = LibraryItem.query.get_or_404(item_id)
    item.archived = True
    db.session.commit()
    flash("Item archived", "warning")
    return redirect(url_for("library.index"))

@library_bp.route("/item/<int:item_id>/delete", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def delete_item(item_id):
    item = LibraryItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash("Item permanently deleted", "danger")
    return redirect(url_for("library.index"))


@library_bp.route("/archived")
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def archived_items():
    items = LibraryItem.query.filter_by(archived=True).order_by(LibraryItem.created_at.desc()).all()
    return render_template("library/archived.html", items=items)

@library_bp.route("/item/<int:item_id>/restore", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def restore_item(item_id):
    item = LibraryItem.query.get_or_404(item_id)
    item.archived = False
    db.session.commit()
    flash("Item restored", "success")
    return redirect(url_for("library.archived_items"))


@library_bp.route("/item/<int:item_id>/unmark_trending", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def unmark_trending(item_id):
    TrendingItem.query.filter_by(item_id=item_id).delete()
    db.session.commit()
    flash("Item unhighlighted","info")
    return redirect(url_for("library.view_item", item_id=item_id))


@library_bp.route("/end_session/<int:item_id>", methods=["POST"])
@login_required
def end_session(item_id):
    # -------------------- [START] Move import inside function --------------------
    from ..activity.models import LibrarySession
    # -------------------- [END] Move import inside function --------------------
    import json, time
    payload = json.loads(request.data or '{}')
    duration = int(payload.get("duration",0))
    session = LibrarySession.query.filter_by(user_id=current_user.id, item_id=item_id).order_by(LibrarySession.id.desc()).first()
    if session and session.ended_at is None:
        session.ended_at = datetime.utcnow()
        session.duration = duration
    else:
        session = LibrarySession(user_id=current_user.id, item_id=item_id, duration=duration, started_at=datetime.utcnow(), ended_at=datetime.utcnow())
        db.session.add(session)
    db.session.commit()
    return ("",204)


@library_bp.route("/progress/<int:item_id>", methods=["POST"], endpoint="progress")
@login_required
def progress_update(item_id):
    """AJAX endpoint to receive and cache video progress in Redis."""
    redis = getattr(current_app, "redis", None)
    if not redis:
        return jsonify({"ok": False, "error": "Redis not configured"}), 503

    payload = request.get_json(silent=True) or {}
    position = payload.get("position")
    duration = payload.get("duration")

    if position is None or duration is None:
        return jsonify({"ok": False, "error": "Missing position or duration"}), 400

    # The background worker will pick this up. Key: "libprog:{user}:{item}"
    redis_key = f"libprog:{current_user.id}:{item_id}"
    redis.hset(redis_key, mapping={
        "position": int(position),
        "duration": int(duration)
    })
    
    return jsonify({"ok": True}), 200



@library_bp.route("/search_items")
@login_required
def search_library_items_for_prereq():
    q = request.args.get("q","").strip()
    if not q: return jsonify([])
    like = f"%{q}%"
    items = (LibraryItem.query
             .filter(LibraryItem.title.ilike(like))
             .order_by(LibraryItem.title.asc()).limit(15).all())
    return jsonify([{"id": i.id, "title": i.title} for i in items])



@library_bp.route("/categories/export")
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def export_categories():
    import io, csv
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id","name"])
    for c in LibraryCategory.query.order_by(LibraryCategory.id.asc()).all():
        writer.writerow([c.id, c.name])
    buf.seek(0)
    from flask import make_response
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename=categories.csv"
    return resp

@library_bp.route("/categories/import", methods=["POST"])
@login_required
@role_required("ADMIN","SUPER_ADMIN")
def import_categories():
    import io, csv
    f = request.files.get("csv")
    if not f:
        flash("No file uploaded","danger")
        return redirect(url_for("library.manage_categories"))
    text = f.stream.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    added, skipped = 0, 0
    for row in reader:
        name = (row.get("name") or "").strip()
        if not name: 
            skipped += 1; continue
        if LibraryCategory.query.filter_by(name=name).first():
            skipped += 1; continue
        db.session.add(LibraryCategory(name=name))
        added += 1
    db.session.commit()
    flash(f"Imported: {added} added, {skipped} skipped","success")
    return redirect(url_for("library.manage_categories"))