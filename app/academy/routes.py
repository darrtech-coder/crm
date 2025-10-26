from flask import render_template, redirect, url_for, request, jsonify, current_app, flash
from flask_login import login_required, current_user
from . import academy_bp
from .models import AcademyCourse, AcademyCourseItem, AcademyModuleStatus
from ..extensions import db
from ..library.models import LibraryItem, LibraryPrerequisite
from ..tests.models import Test, TestSubmission

from ..utils.rbac import role_required
from ..library.models import LibraryItem
from ..tests.models import Test
import os
from werkzeug.utils import secure_filename

from sqlalchemy import func

def module_count(course):
    return len(course.items or [])

def get_module(course, idx):
    items = course.items or []
    if idx < 1 or idx > len(items): return None
    return items[idx-1]

def test_passed(sub, pass_percent, require_review):
    if not sub: return False
    test = sub.test
    total_q = len(test.questions) if test and test.questions else 0
    total_possible = total_q * 5 or 1
    percent = (sub.score or 0) / total_possible * 100
    if percent < (pass_percent or 0):
        return False
    if require_review:
        # Require that all non-MCQ answers have a numeric score (manual graded)
        manual = [a for a in sub.answers if a.question and a.question.type in ("short_text","audio")]
        if manual and any(a.score is None for a in manual):
            return False
    return True

@academy_bp.route("/")
@login_required
def index():
    # Search
    q = request.args.get("q", "").strip()
    query = AcademyCourse.query.filter_by(published=True)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (AcademyCourse.title.ilike(like)) |
            (AcademyCourse.description.ilike(like))
        )
    courses = query.order_by(AcademyCourse.created_at.desc()).all()

    # Per-user progress across courses (done/total; complete if all)
    progress = {}
    if courses:
        course_ids = [c.id for c in courses]

        # Total items per course
        totals_rows = (
            db.session.query(AcademyCourseItem.course_id, func.count(AcademyCourseItem.id))
            .filter(AcademyCourseItem.course_id.in_(course_ids))
            .group_by(AcademyCourseItem.course_id)
            .all()
        )
        totals = {cid: cnt for cid, cnt in totals_rows}

        # Completed items per course for current user
        done_rows = (
            db.session.query(AcademyCourseItem.course_id, func.count(AcademyModuleStatus.id))
            .join(AcademyModuleStatus, AcademyModuleStatus.course_item_id == AcademyCourseItem.id)
            .filter(
                AcademyCourseItem.course_id.in_(course_ids),
                AcademyModuleStatus.user_id == current_user.id
            )
            .group_by(AcademyCourseItem.course_id)
            .all()
        )


        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 12, type=int)
        pagination = query.order_by(AcademyCourse.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        courses = pagination.items

        # compute progress only for courses in this page:
        course_ids = [c.id for c in courses]




        dones = {cid: cnt for cid, cnt in done_rows}

        for cid in course_ids:
            total = int(totals.get(cid, 0) or 0)
            done = int(dones.get(cid, 0) or 0)
            complete = (total > 0 and done >= total)
            percent = int(round((done / total) * 100)) if total else 0
            progress[cid] = {"total": total, "done": done, "complete": complete, "percent": percent}

    return render_template("academy/index.html", courses=courses, q=q, progress=progress, per_page=per_page)

@academy_bp.route("/admin_index")
@login_required
def admin_index():
    courses = AcademyCourse.query.order_by(AcademyCourse.created_at.desc()).all()
    return render_template("academy/admin_index.html", courses=courses)

@academy_bp.route("/course/<int:course_id>")
@login_required
def course_view(course_id):
    course = AcademyCourse.query.get_or_404(course_id)
    return redirect(url_for("academy.module_view", course_id=course.id, idx=1))

@academy_bp.route("/course/<int:course_id>/module/<int:idx>")
@login_required
def module_view(course_id, idx):
    course = AcademyCourse.query.get_or_404(course_id)
    mod = get_module(course, idx)
    if not mod: 
        flash("Module not found","danger")
        return redirect(url_for("academy.course_view", course_id=course.id))

    # compute completion for sidebar (lightweight, derived)
    completed_ids = { s.course_item_id for s in AcademyModuleStatus.query
                      .join(AcademyCourseItem, AcademyCourseItem.id==AcademyModuleStatus.course_item_id)
                      .filter(AcademyModuleStatus.user_id==current_user.id,
                              AcademyCourseItem.course_id==course.id).all() }

    # build module payload
    payload = {"course": course, "mod": mod, "idx": idx,
               "total": module_count(course), "completed_ids": completed_ids}

    # for library modules, optionally enforce LibraryPrerequisite (outside Academy too)
    if mod.type == "library" and mod.library_item_id:
        prereq = LibraryPrerequisite.query.filter_by(item_id=mod.library_item_id).first()
        if prereq:
            # allow if the prereq module already completed in this course OR user viewed prereq item in library before
            prereq_completed = db.session.query(AcademyModuleStatus.id) \
                .join(AcademyCourseItem, AcademyCourseItem.id == AcademyModuleStatus.course_item_id) \
                .filter(AcademyModuleStatus.user_id == current_user.id,
                        AcademyCourseItem.course_id == course.id,
                        AcademyCourseItem.library_item_id == prereq.prereq_item_id).first() is not None
            if not prereq_completed:
                flash("Complete the prerequisite before viewing this module.","warning")
                return redirect(url_for("academy.module_view", course_id=course.id, idx=max(1, idx-1)))

        from ..library.models import LibraryView
        db.session.add(LibraryView(item_id=mod.library_item_id, user_id=current_user.id))
        db.session.commit()



    # For test module, compute readiness (latest submission)
    if mod.type == "test" and mod.test_id:
        latest = (TestSubmission.query
                  .filter_by(test_id=mod.test_id, user_id=current_user.id)
                  .order_by(TestSubmission.submitted_at.desc()).first())
        payload["latest_pass"] = test_passed(latest, mod.pass_percent, mod.require_review)
        payload["latest_score"] = latest.score if latest else None

    return render_template("academy/course.html", **payload)

@academy_bp.route("/course/<int:course_id>/complete/<int:item_id>", methods=["POST"])
@login_required
def mark_complete(course_id, item_id):
    # Mark the module as completed (idempotent)
    mod = AcademyCourseItem.query.get_or_404(item_id)
    exists = AcademyModuleStatus.query.filter_by(user_id=current_user.id, course_item_id=mod.id).first()
    if not exists:
        db.session.add(AcademyModuleStatus(user_id=current_user.id, course_item_id=mod.id))
        db.session.commit()
        
    # --- [FIX] Check if this is the last module ---
    next_idx = mod.position + 1
    total_modules = AcademyCourseItem.query.filter_by(course_id=course_id).count()

    if next_idx > total_modules:
        # If it's the last one, go to the new summary page
        return redirect(url_for("academy.course_summary", course_id=course_id))
    else:
        # Otherwise, go to the next module
        return redirect(url_for("academy.module_view", course_id=course_id, idx=next_idx))


# --- [NEW] Course Summary Route ---
@academy_bp.route("/course/<int:course_id>/summary")
@login_required
def course_summary(course_id):
    course = AcademyCourse.query.get_or_404(course_id)
    
    # Check completion status
    total_modules = len(course.items)
    completed_modules = AcademyModuleStatus.query.join(AcademyCourseItem)\
        .filter(AcademyCourseItem.course_id == course_id, AcademyModuleStatus.user_id == current_user.id)\
        .count()

    is_complete = total_modules > 0 and completed_modules >= total_modules

    return render_template("academy/summary.html", 
                           course=course, 
                           is_complete=is_complete,
                           total_modules=total_modules,
                           completed_modules=completed_modules)




@academy_bp.route("/test_status")
@login_required
def test_status():
    test_id = request.args.get("test_id", type=int)
    pass_req = request.args.get("pass", type=int, default=0)
    require_review = request.args.get("review", type=int, default=0) == 1
    latest = (TestSubmission.query
              .filter_by(test_id=test_id, user_id=current_user.id)
              .order_by(TestSubmission.submitted_at.desc()).first())
    ok = test_passed(latest, pass_req, require_review)
    return jsonify({"passed": ok, "score": (latest.score if latest else None)})




# ---------- Admin: create/edit course ----------
@academy_bp.route("/admin/course/new", methods=["GET", "POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def course_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        desc = request.form.get("description")
        published = bool(request.form.get("published"))
        if not title:
            flash("Title is required","danger")
            return redirect(request.url)

        c = AcademyCourse(title=title, description=desc, created_by=current_user.id, published=published)
        # optional thumbnail upload
        thumb = request.files.get("thumbnail")
        if thumb and thumb.filename:
            upload_dir = os.path.join(current_app.root_path, "..", "uploads", "media", "academy")
            os.makedirs(upload_dir, exist_ok=True)
            fname = secure_filename(thumb.filename)
            thumb_path = os.path.join(upload_dir, fname)
            thumb.save(thumb_path)
            c.thumbnail = os.path.join("academy", fname)  # will serve via presentations.media_file if you prefer

        db.session.add(c)
        db.session.commit()
        flash("Course created","success")
        return redirect(url_for("academy.admin_index"))

    return render_template("academy/course_form.html", course=None)

@academy_bp.route("/admin/course/<int:course_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def course_edit(course_id):
    c = AcademyCourse.query.get_or_404(course_id)
    if request.method == "POST":
        c.title = request.form.get("title","").strip() or c.title
        c.description = request.form.get("description")
        c.published = bool(request.form.get("published"))

        thumb = request.files.get("thumbnail")
        if thumb and thumb.filename:
            upload_dir = os.path.join(current_app.root_path, "..", "uploads", "media", "academy")
            os.makedirs(upload_dir, exist_ok=True)
            fname = secure_filename(thumb.filename)
            thumb_path = os.path.join(upload_dir, fname)
            thumb.save(thumb_path)
            c.thumbnail = os.path.join("academy", fname)

        db.session.commit()
        flash("Course updated","success")
        return redirect(url_for("academy.admin_index"))

    return render_template("academy/course_form.html", course=c)

@academy_bp.route("/admin/course/<int:course_id>/delete", methods=["POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def course_delete(course_id):
    c = AcademyCourse.query.get_or_404(course_id)
    db.session.delete(c)
    db.session.commit()
    flash("Course deleted","warning")
    return redirect(url_for("academy.admin_index"))

# ---------- Admin: manage modules ----------
@academy_bp.route("/admin/course/<int:course_id>/modules", methods=["GET","POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def modules(course_id):
    c = AcademyCourse.query.get_or_404(course_id)
    if request.method == "POST":
        mtype = request.form.get("type")
        lib_id = request.form.get("library_item_id", type=int)
        test_id = request.form.get("test_id", type=int)
        pass_percent = request.form.get("pass_percent", type=int, default=0)
        require_review = "require_review" in request.form

        pos = (c.items[-1].position + 1) if c.items else 1

        mod = AcademyCourseItem(
            course_id=c.id,
            position=pos,
            type=mtype,
            library_item_id=lib_id if mtype=="library" else None,
            test_id=test_id if mtype=="test" else None,
            pass_percent=pass_percent if mtype=="test" else 0,
            require_review=require_review if mtype=="test" else False
        )
        db.session.add(mod)
        db.session.commit()
        flash("Module added","success")
        return redirect(request.url)

    return render_template("academy/modules_form.html", course=c)

@academy_bp.route("/admin/course/<int:course_id>/modules/<int:item_id>/delete", methods=["POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def module_delete(course_id, item_id):
    mod = AcademyCourseItem.query.get_or_404(item_id)
    db.session.delete(mod)
    db.session.commit()
    # re-number positions
    items = AcademyCourseItem.query.filter_by(course_id=course_id).order_by(AcademyCourseItem.position.asc()).all()
    for i, it in enumerate(items, start=1):
        it.position = i
    db.session.commit()
    flash("Module deleted","warning")
    return redirect(url_for("academy.modules", course_id=course_id))

@academy_bp.route("/admin/course/<int:course_id>/modules/<int:item_id>/move", methods=["POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def module_move(course_id, item_id):
    direction = request.form.get("dir")  # 'up' or 'down'
    mod = AcademyCourseItem.query.get_or_404(item_id)
    items = AcademyCourseItem.query.filter_by(course_id=course_id).order_by(AcademyCourseItem.position.asc()).all()
    idx = [i.id for i in items].index(mod.id)

    if direction == "up" and idx > 0:
        items[idx].position, items[idx-1].position = items[idx-1].position, items[idx].position
    elif direction == "down" and idx < len(items)-1:
        items[idx].position, items[idx+1].position = items[idx+1].position, items[idx].position

    db.session.commit()
    flash("Order updated","success")
    return redirect(url_for("academy.modules", course_id=course_id))

# ---------- AJAX search for library items & tests ----------
@academy_bp.route("/search/library_items")
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def search_library_items():
    q = request.args.get("q","").strip()
    if not q: return jsonify([])
    like = f"%{q}%"
    items = LibraryItem.query.filter(LibraryItem.title.ilike(like)).order_by(LibraryItem.title.asc()).limit(15).all()
    return jsonify([{"id": i.id, "title": i.title} for i in items])

@academy_bp.route("/search/tests")
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def search_tests():
    q = request.args.get("q","").strip()
    if not q: return jsonify([])
    like = f"%{q}%"
    tests = Test.query.filter(Test.title.ilike(like)).order_by(Test.title.asc()).limit(15).all()
    return jsonify([{"id": t.id, "title": t.title} for t in tests])




@academy_bp.route("/admin/course/<int:course_id>/modules/reorder", methods=["POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def modules_reorder(course_id):
    data = request.get_json(silent=True) or {}
    order = data.get("order", [])
    if not isinstance(order, list):
        return jsonify({"ok": False, "error": "Invalid payload"}), 400

    items = AcademyCourseItem.query.filter_by(course_id=course_id).all()
    lookup = {i.id: i for i in items}
    pos = 1
    for iid in order:
        if iid in lookup:
            lookup[iid].position = pos
            pos += 1
    db.session.commit()
    return jsonify({"ok": True, "count": pos - 1})

