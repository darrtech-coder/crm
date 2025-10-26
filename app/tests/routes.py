from flask import render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from . import tests_bp
from ..extensions import db
from .models import Test, TestQuestion, TestOption, TestSubmission, TestAnswer, TestPrerequisite
from datetime import datetime, timedelta # Add timedelta
from werkzeug.utils import secure_filename
import os, json, base64, uuid

import zipfile, tempfile, json, csv, re, os
from flask import send_file, after_this_request

# -------------------- [START] Add 'func' to this import --------------------
from sqlalchemy import or_, func
# -------------------- [END] Add 'func' to this import --------------------

# up_dir = os.path.join(current_app.root_path, "..", "uploads", "audio_answers")

def slugify(text):
    text = text or ""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "untitled"

# Manager/Admin create tests
@tests_bp.route("/")
@login_required
def index():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 12, type=int)
    if current_user.role in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        q = Test.query.order_by(Test.title.asc())
    else:
        now = datetime.utcnow()
        q = Test.query.filter(
            or_(Test.status=='public', (Test.status=='scheduled') & (Test.publish_at <= now))
        ).order_by(Test.title.asc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    return render_template("tests/index.html", tests=pagination.items, pagination=pagination, per_page=per_page)






@tests_bp.route("/create", methods=["GET", "POST"])
@login_required
def create():
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.index"))

    if request.method == "POST":
        title = request.form["title"]
        desc = request.form.get("description")
        # -------------------- [START] Handle time_limit on create --------------------
        time_limit_str = request.form.get("time_limit")
        time_limit = int(time_limit_str) if time_limit_str and time_limit_str.isdigit() else None
        
        t = Test(title=title, description=desc, created_by=current_user.id, time_limit=time_limit)
        # -------------------- [END] Handle time_limit on create --------------------
        db.session.add(t)
        db.session.commit()
        flash("Test created", "success")
        return redirect(url_for("tests.edit", test_id=t.id))

    return render_template("tests/create.html")


@tests_bp.route("/<int:test_id>/edit", methods=["GET","POST"])
@login_required
def edit(test_id):
    test = Test.query.get_or_404(test_id)
    if request.method == "POST":
        test.title = request.form["title"]
        test.description = request.form.get("description")
        
        # -------------------- [START] Full Implementation of Metadata Saving --------------------
        time_limit_str = request.form.get("time_limit")
        test.time_limit = int(time_limit_str) if time_limit_str and time_limit_str.isdigit() else None
        
        test.status = request.form.get("status", "draft")
        
        publish_at_str = request.form.get("publish_at")
        if publish_at_str:
            try:
                # The browser sends datetime-local in 'YYYY-MM-DDTHH:MM' format
                test.publish_at = datetime.fromisoformat(publish_at_str)
            except ValueError:
                test.publish_at = None # Or handle error
        else:
            test.publish_at = None
        # -------------------- [END] Full Implementation of Metadata Saving --------------------

        db.session.commit()
        flash("Test metadata updated successfully", "success")
        return redirect(url_for('tests.edit', test_id=test.id))
    
    return render_template("tests/edit_inline.html", test=test, library_items=LibraryItem.query.all())





@tests_bp.route("/<int:test_id>/take", methods=["GET", "POST"])
@login_required
def take(test_id):
    test = Test.query.get_or_404(test_id)

    # --- Course requirement checks ---
    requirements = TestCourseRequirement.query.filter_by(test_id=test_id).all()
    unmet_required = []
    for r in requirements:
        done = LibraryView.query.filter_by(user_id=current_user.id, item_id=r.course_id).first() is not None
        if r.requirement_type == "required" and not done:
            unmet_required.append(r.course)

    if unmet_required:
        flash("You must complete the required courses before this test.", "danger")
        return render_template("tests/requirements_block.html", required=unmet_required)

    # --- POST: final submission ---
    if request.method == "POST":
        submission = TestSubmission(test_id=test.id, user_id=current_user.id, submitted_at=datetime.utcnow())
        db.session.add(submission)
        db.session.flush()

        # --- Iterate through questions and save answers ---
        for q in test.questions:
            qid_str = f"q{q.id}"
            
            # -------------------- [START] CORRECTED IF/ELIF BLOCK --------------------
            if q.type == "mcq":
                opt_id = request.form.get(qid_str)
                if opt_id and opt_id.isdigit():
                    option = TestOption.query.get(int(opt_id))
                    if option:
                        db.session.add(TestAnswer(
                            submission_id=submission.id, question_id=q.id,
                            selected_option=option.id, is_correct=option.is_correct
                        ))
            elif q.type == "short_text":
                txt = request.form.get(qid_str)
                if txt and txt.strip():
                    db.session.add(TestAnswer(
                        submission_id=submission.id, question_id=q.id, answer_text=txt.strip()
                    ))
            elif q.type == "audio":
                audio_filename = None
                
                # Case 1: Recorded audio (sends a filename)
                temp_filename = request.form.get(f"recorded_audio_{q.id}")
                if temp_filename:
                    temp_path = os.path.join(current_app.root_path, "..", "uploads", "media", "audio_temp", temp_filename)
                    final_dir = os.path.join(current_app.root_path, "..", "uploads", "media", "audio_answers")
                    os.makedirs(final_dir, exist_ok=True)
                    final_path = os.path.join(final_dir, temp_filename)

                    if os.path.exists(temp_path):
                        os.rename(temp_path, final_path)
                        audio_filename = temp_filename
                    else:
                        current_app.logger.warning(f"Temporary audio file not found: {temp_path}")

                # Case 2: Uploaded audio file
                uploaded_file = request.files.get(qid_str)
                if uploaded_file and uploaded_file.filename:
                    final_dir = os.path.join(current_app.root_path, "..", "uploads", "media", "audio_answers")
                    os.makedirs(final_dir, exist_ok=True)
                    audio_filename = secure_filename(f"{uuid.uuid4().hex}_{uploaded_file.filename}")
                    uploaded_file.save(os.path.join(final_dir, audio_filename))
                
                # if audio_filename:
                db.session.add(TestAnswer(
                    submission_id=submission.id, question_id=q.id,
                    answer_audio=audio_filename, score=0.0
                ))
            # -------------------- [END] CORRECTED IF/ELIF BLOCK --------------------

        # --- Auto-grade MCQs ---
        total_score = 0.0
        db.session.flush()
        for ans in submission.answers:
            if ans.question.type == "mcq":
                ans.score = 5.0 if ans.is_correct else 0.0
            total_score += ans.score or 0.0
        submission.score = round(total_score, 1)

        # --- Clean up autosave draft ---
        redis_key = f"autosave:{test.id}:{current_user.id}"
        try: current_app.redis.delete(redis_key)
        except Exception: pass
        
        db.session.commit()

        # --- Render summary page ---
        total_questions = len(test.questions)
        avg_score = db.session.query(func.avg(TestSubmission.score)).filter_by(test_id=test.id).scalar()
        
        mcq_answers = [a for a in submission.answers if a.question.type == "mcq"]
        non_mcq_answers = [a for a in submission.answers if a.question.type != "mcq"]
        min_mcq = sum(a.score or 0 for a in mcq_answers)
        max_mcq = min_mcq + (len(non_mcq_answers) * 5.0)
        total_possible = total_questions * 5.0

        return render_template(
            "tests/post_summary.html", test=test, submission=submission,
            total_questions=total_questions, avg_score=avg_score,
            min_mcq=min_mcq, max_mcq=max_mcq, total_possible=total_possible
        )
    
    # --- GET request logic ...
    redis_key = f"autosave:{test.id}:{current_user.id}"
    autosaved, start_question, remaining_time = {}, 1, None
    try:
        cached = current_app.redis.get(redis_key)
        if cached:
            saved_data = json.loads(cached)
            autosaved = saved_data.get("answers", {})
            start_question = saved_data.get("current_question", 1)
            if test.time_limit and 'remaining_time' in saved_data:
                remaining_time = saved_data.get('remaining_time')
            flash("Restored your last session — continue where you left off.", "info")
    except Exception as e:
        current_app.logger.warning(f"Could not restore autosave for user {current_user.id}: {e}")
        flash("Could not restore previous session due to an error.", "warning")

    return render_template("tests/take.html", test=test, autosaved=autosaved,
                           start_question=start_question, remaining_time=remaining_time)




# === Review all submissions for a given test ===
@tests_bp.route("/submissions/<int:test_id>")
@login_required
def review_submissions(test_id):
    test = Test.query.get_or_404(test_id)
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.index"))

    submissions = (
        TestSubmission.query.filter_by(test_id=test.id)
        .order_by(TestSubmission.submitted_at.desc())
        .all()
    )
    return render_template("tests/submissions.html", test=test, submissions=submissions)


# === Detailed review page ===
@tests_bp.route("/submission/<int:submission_id>", methods=["GET", "POST"])
@login_required
def grade_submission(submission_id):
    sub = TestSubmission.query.get_or_404(submission_id)
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.index"))

    if request.method == "POST":
        # Manual marks come in as q_<id>
        total_score = 0.0
        for ans in sub.answers:
            if ans.question.type == "mcq":
                # already auto‑graded, nothing to change
                total_score += 5.0 if (ans.is_correct) else 0.0
            else:
                val = request.form.get(f"q_{ans.id}", "")
                try:
                    mark = round(float(val), 1)
                    if mark < 0:
                        mark = 0
                    elif mark > 5:
                        mark = 5
                except ValueError:
                    mark = 0
                ans.is_correct = None  # keeps db consistent
                ans.score = mark       # store numeric mark
                total_score += mark

        sub.score = round(total_score, 1)
        db.session.commit()
        flash(f"Submission graded — total {sub.score:.1f} points", "success")
        return redirect(url_for("tests.review_submissions", test_id=sub.test_id))

    # If GET, show page
    return render_template("tests/grade_submission.html", submission=sub)


# === Add a question ===
@tests_bp.route("/<int:test_id>/add_question", methods=["POST"])
@login_required
def add_question(test_id):
    test = Test.query.get_or_404(test_id)
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        flash("Permission denied", "danger")
        return redirect(url_for("tests.edit", test_id=test.id))

    qtype = request.form.get("type", "mcq")
    qtext = request.form.get("question", "").strip()

    if not qtext:
        flash("Please enter a question.", "danger")
        return redirect(url_for("tests.edit", test_id=test.id))

    q = TestQuestion(test_id=test.id, type=qtype, question=qtext)
    db.session.add(q)
    db.session.flush()  # ensure q.id

    # --- optional: handle new media when creating ---
    file = request.files.get("media")
    if file and file.filename:
        upload_dir = os.path.join(current_app.root_path, "..", "uploads", "question_media")
        os.makedirs(upload_dir, exist_ok=True)
        fname = secure_filename(file.filename)
        file.save(os.path.join(upload_dir, fname))
        q.media_path = fname

    if qtype == "mcq":
        # Options come as repeated form fields
        options = request.form.getlist("option")
        flags = request.form.getlist("is_correct")
        for i, text in enumerate(options):
            if text.strip():
                correct = (flags[i] == "on") if i < len(flags) else False
                opt = TestOption(question_id=q.id, text=text.strip(), is_correct=correct)
                db.session.add(opt)

    try:
        db.session.commit()
        flash("Question saved successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving question: {e}", "danger")
        current_app.logger.exception(e)
    return redirect(url_for("tests.edit", test_id=test.id))





# === Edit a question (with media upload/delete) ===
@tests_bp.route("/question/<int:qid>/edit", methods=["GET", "POST"])
@login_required
def edit_question(qid):
    q = TestQuestion.query.get_or_404(qid)
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        abort(403)

    upload_dir = os.path.join(current_app.root_path, "..", "uploads", "question_media")
    os.makedirs(upload_dir, exist_ok=True)

    if request.method == "POST":
        # --- Delete media button pressed
        if "delete_media" in request.form:
            if q.media_path:
                try:
                    os.remove(os.path.join(upload_dir, q.media_path))
                except FileNotFoundError:
                    pass
                q.media_path = None
                db.session.commit()
                flash("Media removed", "info")
            return redirect(url_for("tests.edit", test_id=q.test_id))

        # --- Update question text
        q.question = request.form.get("question", "")

        # --- Handle media upload/replacement
        file = request.files.get("media")
        if file and file.filename:
            fname = secure_filename(file.filename)

            # remove old file if exists
            if q.media_path:
                try:
                    os.remove(os.path.join(upload_dir, q.media_path))
                except FileNotFoundError:
                    pass

            # save new file
            file.save(os.path.join(upload_dir, fname))
            q.media_path = fname

        # --- Update options (if MCQ)
        if q.type == "mcq":
            TestOption.query.filter_by(question_id=q.id).delete()
            options = request.form.getlist("option")
            flags = request.form.getlist("is_correct")
            for i, text in enumerate(options):
                if text.strip():
                    correct = (flags[i] == "on") if i < len(flags) else False
                    db.session.add(TestOption(
                        question_id=q.id,
                        text=text.strip(),
                        is_correct=correct
                    ))

        db.session.commit()
        flash("Question updated successfully", "success")
        return redirect(url_for("tests.edit", test_id=q.test_id))

    return render_template("tests/edit_question.html", q=q)





@tests_bp.route("/question/<int:qid>/delete", methods=["POST"])
@login_required
def delete_question(qid):
    q = TestQuestion.query.get_or_404(qid)
    test_id = q.test_id
    db.session.delete(q)
    db.session.commit()
    flash("Question deleted","warning")
    return redirect(url_for("tests.edit", test_id=test_id))


@tests_bp.route("/<int:test_id>/delete", methods=["POST"])
@login_required
def delete_test(test_id):
    t=Test.query.get_or_404(test_id)
    db.session.delete(t); db.session.commit()
    flash("Test deleted","danger")
    return redirect(url_for("tests.index"))


@tests_bp.route("/<int:test_id>/archive", methods=["POST"])
def archive_test(test_id):
    t=Test.query.get_or_404(test_id)
    t.status="archived"; db.session.commit()
    flash("Test archived","warning")
    return redirect(url_for("tests.index"))

@tests_bp.route("/<int:test_id>/restore", methods=["POST"])
def restore_test(test_id):
    t=Test.query.get_or_404(test_id)
    t.status="draft"; db.session.commit()
    flash("Test restored to draft","success")
    return redirect(url_for("tests.index"))



from flask import send_from_directory

@tests_bp.route("/media/<path:filename>")
@login_required
def question_media(filename):
    """Serve uploaded question media (images/videos/audio)."""
    media_dir = os.path.join(current_app.root_path, "..", "uploads", "question_media")
    return send_from_directory(media_dir, filename)


@tests_bp.route("/<int:test_id>/question/<int:index>")
@login_required
def get_question(test_id, index):
    """Return one question in JSON format for AJAX render."""
    test = Test.query.get_or_404(test_id)
    questions = TestQuestion.query.filter_by(test_id=test_id).order_by(TestQuestion.id).all()
    total = len(questions)
    if index < 1 or index > total:
        return jsonify({"error": "Invalid index"}), 404

    q = questions[index - 1]
    return jsonify({
        "id": q.id,
        "index": index,
        "total": total,
        "question": q.question,
        "type": q.type,
        "media": q.media_path,     # ✅ use the proper column name
        "options": [{"id": o.id, "text": o.text} for o in q.options]
    })



from ..library.models import LibraryItem
from .models import TestCourseRequirement

@tests_bp.route("/<int:test_id>/add_course_req", methods=["POST"])
@login_required
def add_course_req(test_id):
    if current_user.role not in ("MANAGER","ADMIN", "SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.edit", test_id=test_id))

    title = request.form.get("q")
    req_type = request.form.get("type")
    course = LibraryItem.query.filter_by(title=title).first()
    if not course:
        flash("Course not found", "danger")
        return redirect(url_for("tests.edit", test_id=test_id))

    existing = TestCourseRequirement.query.filter_by(test_id=test_id, course_id=course.id).first()
    if existing:
        existing.requirement_type = req_type
    else:
        db.session.add(TestCourseRequirement(test_id=test_id, course_id=course.id, requirement_type=req_type))
    db.session.commit()
    flash(f"'{course.title}' added as {req_type.capitalize()}", "success")
    return redirect(url_for("tests.edit", test_id=test_id))


@tests_bp.route("/<int:test_id>/delete_course_req/<int:req_id>", methods=["POST"])
@login_required
def delete_course_req(test_id, req_id):
    if current_user.role not in ("MANAGER","ADMIN","SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.edit", test_id=test_id))

    req = TestCourseRequirement.query.get_or_404(req_id)
    db.session.delete(req)
    db.session.commit()
    flash("Course requirement removed", "info")
    return redirect(url_for("tests.edit", test_id=test_id))


@tests_bp.route("/search_courses")
@login_required
def search_courses():
    q = request.args.get("q", "").strip()
    results = []
    if q:
        results = LibraryItem.query.filter(LibraryItem.title.ilike(f"%{q}%")).limit(10).all()
    return jsonify([{"id":i.id, "title":i.title} for i in results])


@tests_bp.route("/question/<int:test_id>/<int:index>")
@login_required
def get_question_json(test_id, index):
    q = TestQuestion.query.filter_by(test_id=test_id).order_by(TestQuestion.id).offset(index-1).first_or_404()
    return jsonify({
        "id": q.id,
        "text": q.question,
        "type": q.type,
        "options": [{"id":o.id,"text":o.text} for o in q.options],
        "media": q.media_path
    })


from ..library.models import LibraryView, LibraryItem
from .models import TestCourseRequirement

@tests_bp.route("/<int:test_id>/ready")
@login_required
def ready(test_id):
    """Pre‑summary screen before starting the test."""
    test = Test.query.get_or_404(test_id)
    requirements = TestCourseRequirement.query.filter_by(test_id=test.id).all()


    # --- new: prerequisite tests enforcement ---
    from .models import TestPrerequisite, TestSubmission

    # Find all prerequisite test IDs for this test
    prereq_links = TestPrerequisite.query.filter_by(test_id=test.id).all()
    prereq_ids = [p.prereq_test_id for p in prereq_links]

    # --- Determine which tests the user has completed (>= 60 %)
    completed_ids = []
    user_subs = TestSubmission.query.filter_by(user_id=current_user.id).all()

    for s in user_subs:
        if not s.test or not s.test.questions:
            continue
        total_questions = len(s.test.questions)
        total_possible = total_questions * 5.0
        percent = (s.score or 0) / total_possible * 100
        if percent >= 60:     # ✅ threshold in percentage
            completed_ids.append(s.test_id)


    # Identify unmet prerequisites (those the user hasn't completed)
    unmet_prereqs = [pid for pid in prereq_ids if pid not in completed_ids]



    # Determine which course IDs this user has viewed/completed
    completed_ids = [v.item_id for v in LibraryView.query.filter_by(user_id=current_user.id).all()]

    blocked, recommended_pending = False, False
    for r in requirements:
        done = r.course_id in completed_ids
        if r.requirement_type == "required" and not done:
            blocked = True
        elif r.requirement_type == "recommended" and not done:
            recommended_pending = True

    # Basic stats
    question_count = len(test.questions)
    avg_score = db.session.query(db.func.avg(TestSubmission.score)).filter_by(test_id=test.id).scalar()


    # --- block if prerequisite tests incomplete ---
    blocked_prereqs = []
    if unmet_prereqs:
        blocked_prereqs = Test.query.filter(Test.id.in_(unmet_prereqs)).all()
        flash("You must complete all prerequisite tests before taking this one.", "danger")

    return render_template(
        "tests/pre_summary.html",
        test=test,
        requirements=requirements,
        completed_ids=completed_ids,
        blocked=blocked,
        recommended_pending=recommended_pending,
        question_count=question_count,
        avg_score=avg_score,
        blocked_prereqs=blocked_prereqs   # <-- new
    )

# ------------------------------------------------------------------
# AUTOSAVE endpoint  (handles AJAX calls every 30 s from frontend)
# ------------------------------------------------------------------
@tests_bp.route("/<int:test_id>/autosave", methods=["POST"])
@login_required
def autosave(test_id):
    data = request.get_json(silent=True) or {}
    answers = data.get("answers", {})

    # -------------------- [START] Update autosave payload --------------------
    key = f"autosave:{test_id}:{current_user.id}"
    payload = json.dumps({
        "answers": data.get("answers", {}),
        "current_question": data.get("current_question"),
        "remaining_time": data.get("remaining_time"),
        "saved_at": datetime.utcnow().isoformat()
    })

    # Store in Redis for 2 hours (7200s) + test time limit to be safe
    test = Test.query.get(test_id)
    expiry_seconds = 7200 + ((test.time_limit or 0) * 60)
    
    current_app.redis.setex(key, timedelta(seconds=expiry_seconds), payload)
    # -------------------- [END] Update autosave payload --------------------


    return jsonify({"ok": True, "saved_at": datetime.utcnow().isoformat()})



@tests_bp.route("/<int:test_id>/result")
@login_required
def test_result(test_id):
    from sqlalchemy import func

    test = Test.query.get_or_404(test_id)
    sub = (TestSubmission.query
           .filter_by(test_id=test.id, user_id=current_user.id)
           .order_by(TestSubmission.submitted_at.desc())
           .first())
    if not sub:
        flash("No result found for this test yet.", "warning")
        return redirect(url_for("tests.index"))

    mcq_answers = [a for a in sub.answers if a.question.type == "mcq"]
    min_mcq = sum(a.score or 0 for a in mcq_answers)
    max_mcq = len(mcq_answers) * 5.0
    total_possible = len(test.questions) * 5.0
    total_questions = len(test.questions)
    avg_score = db.session.query(func.avg(TestSubmission.score)).filter_by(test_id=test.id).scalar()

    return render_template(
        "tests/live_results.html",
        test=test,
        submission=sub,
        min_mcq=min_mcq,
        max_mcq=max_mcq,
        total_possible=total_possible,
        total_questions=total_questions,
        avg_score=avg_score
    )



@tests_bp.route("/<int:test_id>/add_questions_bulk", methods=["POST"])
@login_required
def add_questions_bulk(test_id):
    """Save multiple new questions at once."""
    test = Test.query.get_or_404(test_id)
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        flash("Permission denied", "danger")
        return redirect(url_for("tests.edit", test_id=test.id))

    # ----------------------------------------------------------
    # parse multiple blocks from the request
    # names repeat: type, question, media, option, is_correct
    # group them by order in the list
    # ----------------------------------------------------------
    types      = request.form.getlist("type")
    questions  = request.form.getlist("question")
    media_list = request.files.getlist("media")
    index = 0
    for qtext, qtype in zip(questions, types):
        if not qtext.strip():
            continue
        q = TestQuestion(test_id=test.id, type=qtype, question=qtext.strip())

        # attach media if uploaded
        if index < len(media_list):
            file = media_list[index]
            if file and file.filename:
                fname = secure_filename(file.filename)
                upload_dir = os.path.join(
                    current_app.root_path, "..", "uploads", "question_media"
                )
                os.makedirs(upload_dir, exist_ok=True)
                file.save(os.path.join(upload_dir, fname))
                q.media_path = fname
        index += 1
        db.session.add(q)
        db.session.flush()   # get q.id for options below

        # collect relevant options for this question
        option_keys = [k for k in request.form if k.startswith(f"option_{qtype}{index-1}_")]
        if qtype == "mcq":
            total_opts = request.form.getlist("option")
            flags = request.form.getlist("is_correct")
            for text, flag in zip(total_opts, flags):
                if text.strip():
                    db.session.add(
                        TestOption(
                            question_id=q.id,
                            text=text.strip(),
                            is_correct=(flag == "on")
                        )
                    )

    db.session.commit()
    flash(f"Added {len(questions)} new question(s).", "success")
    return redirect(url_for("tests.edit", test_id=test.id))



@tests_bp.route("/<int:test_id>/add_prerequisite", methods=["POST"])
@login_required
def add_prerequisite(test_id):
    if current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.edit", test_id=test_id))

    prereq_title = request.form.get("prereq")
    prereq_test = Test.query.filter_by(title=prereq_title).first()
    if not prereq_test:
        flash("Prerequisite test not found.", "danger")
        return redirect(url_for("tests.edit", test_id=test_id))

    # prevent duplicates / self‑reference
    if prereq_test.id == test_id:
        flash("A test cannot require itself.", "warning")
        return redirect(url_for("tests.edit", test_id=test_id))

    existing = TestPrerequisite.query.filter_by(
        test_id=test_id, prereq_test_id=prereq_test.id
    ).first()
    if existing:
        flash("That prerequisite already exists.", "info")
        return redirect(url_for("tests.edit", test_id=test_id))

    count = TestPrerequisite.query.filter_by(test_id=test_id).count()
    if count >= 2:
        flash("You can assign a maximum of two prerequisite tests.", "warning")
        return redirect(url_for("tests.edit", test_id=test_id))

    db.session.add(TestPrerequisite(test_id=test_id, prereq_test_id=prereq_test.id))
    db.session.commit()
    flash(f"Added prerequisite: {prereq_test.title}", "success")
    return redirect(url_for("tests.edit", test_id=test_id))


@tests_bp.route("/<int:test_id>/delete_prerequisite/<int:pid>", methods=["POST"])
@login_required
def delete_prerequisite(test_id, pid):
    if current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.edit", test_id=test_id))

    prereq = TestPrerequisite.query.get_or_404(pid)
    db.session.delete(prereq)
    db.session.commit()
    flash("Prerequisite removed.", "info")
    return redirect(url_for("tests.edit", test_id=test_id))


@tests_bp.route("/search_tests")
@login_required
def search_tests():
    """AJAX autocomplete for prerequisite or linked tests — excludes archived and current test."""
    q = request.args.get("q", "").strip()
    current_id = request.args.get("current_id", type=int)

    if not q:
        return jsonify([])

    query = Test.query.filter(
        Test.status != "archived",
        Test.title.ilike(f"%{q}%")
    )

    # 🚫 Exclude the test currently being edited
    if current_id:
        query = query.filter(Test.id != current_id)

    results = query.order_by(Test.title.asc()).limit(10).all()
    return jsonify([{"id": t.id, "title": t.title} for t in results])



@tests_bp.route("/submission/<int:submission_id>/result_data")
@login_required
def submission_result_data(submission_id):
    """Return up‑to‑date marks for a submission in JSON (for live updates)."""
    sub = TestSubmission.query.get_or_404(submission_id)
    avg_score = (
        db.session.query(db.func.avg(TestSubmission.score))
        .filter_by(test_id=sub.test_id)
        .scalar()
    )
    total_q = len(sub.test.questions)
    total_possible = total_q * 5
    percent = (sub.score or 0) / total_possible * 100 if total_possible else 0

    return jsonify({
        "total_questions": total_q,
        "your_score": f"{sub.score:.1f}",
        "avg_score": f"{avg_score or 0:.1f}",
        "percent": f"{percent:.1f}",
        "answers": [
            {
                "index": idx + 1,
                "text": a.question.question,
                "type": a.question.type,
                "score": a.score or 0,
                "is_correct": a.is_correct,
            }
            for idx, a in enumerate(sub.answers)
        ],
        "updated_at": sub.submitted_at.isoformat()
    })



# -------------------- [START] NEW ROUTE FOR IMMEDIATE AUDIO UPLOAD --------------------
@tests_bp.route("/upload_chunk", methods=["POST"])
@login_required
def upload_audio_chunk():
    if 'audio_blob' not in request.files:
        return jsonify({"ok": False, "error": "No audio blob in request"}), 400

    file = request.files['audio_blob']
    
    # Use a temporary directory for these chunks
    temp_dir = os.path.join(current_app.root_path, "..", "uploads", "media", "audio_temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    # Create a unique filename
    filename = f"{current_user.id}_{uuid.uuid4().hex}.webm"
    save_path = os.path.join(temp_dir, filename)

    try:
        file.save(save_path)
        # Return the filename so the frontend can store it
        return jsonify({"ok": True, "filename": filename})
    except Exception as e:
        current_app.logger.error(f"Could not save audio chunk: {e}")
        return jsonify({"ok": False, "error": "Server failed to save audio"}), 500
# -------------------- [END] NEW ROUTE FOR IMMEDIATE AUDIO UPLOAD --------------------



@tests_bp.route("/admin/clear_submissions", methods=["GET","POST"])
@login_required
def clear_submissions():
    if current_user.role != "SUPER_ADMIN":
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.index"))

    tests = Test.query.order_by(Test.title.asc()).all()

    if request.method == "POST":
        test_id = request.form.get("test_id")  # "all" or int
        age = request.form.get("age", "all")   # all, 7d, 30d, 90d

        cutoff = None
        now = datetime.utcnow()
        if age == "7d": cutoff = now - timedelta(days=7)
        elif age == "30d": cutoff = now - timedelta(days=30)
        elif age == "90d": cutoff = now - timedelta(days=90)

        subs_q = TestSubmission.query
        if test_id and test_id != "all":
            subs_q = subs_q.filter(TestSubmission.test_id == int(test_id))
        if cutoff:
            subs_q = subs_q.filter(TestSubmission.submitted_at < cutoff)

        # Collect IDs first
        sub_ids = [sid for (sid,) in subs_q.with_entities(TestSubmission.id).all()]
        if not sub_ids:
            flash("No submissions matched your criteria.", "info")
            return redirect(url_for("tests.clear_submissions"))

        # Delete answers and then submissions (bulk, avoiding ORM cascade)
        deleted_ans = TestAnswer.query.filter(TestAnswer.submission_id.in_(sub_ids)).delete(synchronize_session=False)
        deleted_sub = TestSubmission.query.filter(TestSubmission.id.in_(sub_ids)).delete(synchronize_session=False)
        db.session.commit()

        flash(f"Deleted {deleted_sub} submissions and {deleted_ans} answers.", "success")
        return redirect(url_for("tests.clear_submissions"))

    return render_template("tests/clear_submissions.html", tests=tests)



@tests_bp.route("/<int:test_id>/export_answers_zip")
@login_required
def export_test_answers_zip(test_id):
    # Managers/Admins/Super Admins can export all submissions for a test
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.index"))

    test = Test.query.get_or_404(test_id)
    submissions = (TestSubmission.query
                   .filter_by(test_id=test.id)
                   .order_by(TestSubmission.submitted_at.desc())
                   .all())
    if not submissions:
        flash("No submissions to export for this test.", "info")
        return redirect(url_for("tests.review_submissions", test_id=test.id))

    # Build question index map for stable order/index
    q_index = {q.id: i+1 for i, q in enumerate(test.questions or [])}

    # Prepare temp zip
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    zip_path = tmp.name
    tmp.close()

    root = f"test_{test.id}_{slugify(test.title)}"
    audio_dir = os.path.join(current_app.root_path, "..", "uploads", "media", "audio_answers")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Optional: include a top-level manifest
        test_manifest = {
            "test_id": test.id,
            "title": test.title,
            "question_count": len(test.questions or []),
            "submission_count": len(submissions),
            "exported_at": datetime.utcnow().isoformat()
        }
        zf.writestr(f"{root}/manifest.json", json.dumps(test_manifest, indent=2))

        for sub in submissions:
            uname = (sub.user.username if sub.user else f"user_{sub.user_id}") or f"user_{sub.user_id}"
            sub_dir = f"{root}/submission_{sub.id}__{slugify(uname)}/"

            total_q = len(test.questions or [])
            total_possible = total_q * 5.0 if total_q else 0.0
            percent = ((sub.score or 0.0) / total_possible * 100.0) if total_possible else 0.0

            meta = {
                "submission_id": sub.id,
                "user_id": sub.user_id,
                "username": getattr(sub.user, "username", None),
                "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
                "time_spent_sec": sub.time_spent,
                "score": sub.score,
                "percent": round(percent, 1),
                "total_questions": total_q,
                "total_possible": total_possible
            }
            zf.writestr(sub_dir + "submission.json", json.dumps(meta, indent=2))

            # answers.csv
            sio = tempfile.SpooledTemporaryFile(mode="w+", newline="", max_size=1024*1024)
            writer = csv.writer(sio)
            writer.writerow(["q_index", "question_id", "type", "question_text", "is_correct", "score", "selected_option", "answer_text", "audio_file"])
            for ans in sub.answers:
                q = ans.question
                qidx = q_index.get(q.id)
                selected_text = ans.option.text if ans.option else None
                row = [
                    qidx,
                    q.id if q else None,
                    q.type if q else None,
                    q.question if q else None,
                    ans.is_correct,
                    ans.score,
                    selected_text,
                    ans.answer_text,
                    ans.answer_audio
                ]
                writer.writerow(row)
            sio.seek(0)
            zf.writestr(sub_dir + "answers.csv", sio.read())
            sio.close()

            # Copy audio files
            for ans in sub.answers:
                if ans.answer_audio:
                    src = os.path.join(audio_dir, ans.answer_audio)
                    if os.path.exists(src):
                        zf.write(src, arcname=sub_dir + "audio/" + ans.answer_audio)
                    else:
                        # leave a note if missing
                        miss = {"missing_audio": ans.answer_audio, "note": "File not found on server"}
                        zf.writestr(sub_dir + "audio/_missing_" + slugify(ans.answer_audio) + ".json", json.dumps(miss, indent=2))

    @after_this_request
    def cleanup(response):
        try:
            os.remove(zip_path)
        except Exception:
            pass
        return response

    fname = f"test_{test.id}_{slugify(test.title)}_answers.zip"
    return send_file(zip_path, as_attachment=True, download_name=fname)


@tests_bp.route("/submission/<int:submission_id>/export_zip")
@login_required
def export_submission_zip(submission_id):
    # Owner can export their own submission; admins/managers can export any
    sub = TestSubmission.query.get_or_404(submission_id)
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN") and sub.user_id != current_user.id:
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.index"))

    test = sub.test
    q_index = {q.id: i+1 for i, q in enumerate(test.questions or [])}

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    zip_path = tmp.name
    tmp.close()

    root = f"submission_{sub.id}"
    audio_dir = os.path.join(current_app.root_path, "..", "uploads", "media", "audio_answers")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        total_q = len(test.questions or [])
        total_possible = total_q * 5.0 if total_q else 0.0
        percent = ((sub.score or 0.0) / total_possible * 100.0) if total_possible else 0.0

        meta = {
            "test_id": test.id,
            "test_title": test.title,
            "submission_id": sub.id,
            "user_id": sub.user_id,
            "username": getattr(sub.user, "username", None),
            "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
            "time_spent_sec": sub.time_spent,
            "score": sub.score,
            "percent": round(percent, 1),
            "total_questions": total_q,
            "total_possible": total_possible
        }
        zf.writestr(root + "/submission.json", json.dumps(meta, indent=2))

        # answers.csv
        sio = tempfile.SpooledTemporaryFile(mode="w+", newline="", max_size=1024*1024)
        writer = csv.writer(sio)
        writer.writerow(["q_index", "question_id", "type", "question_text", "is_correct", "score", "selected_option", "answer_text", "audio_file"])
        for ans in sub.answers:
            q = ans.question
            qidx = q_index.get(q.id)
            selected_text = ans.option.text if ans.option else None
            writer.writerow([
                qidx,
                q.id if q else None,
                q.type if q else None,
                q.question if q else None,
                ans.is_correct,
                ans.score,
                selected_text,
                ans.answer_text,
                ans.answer_audio
            ])
        sio.seek(0)
        zf.writestr(root + "/answers.csv", sio.read())
        sio.close()

        # audio files
        for ans in sub.answers:
            if ans.answer_audio:
                src = os.path.join(audio_dir, ans.answer_audio)
                if os.path.exists(src):
                    zf.write(src, arcname=root + "/audio/" + ans.answer_audio)
                else:
                    miss = {"missing_audio": ans.answer_audio, "note": "File not found on server"}
                    zf.writestr(root + "/audio/_missing_" + slugify(ans.answer_audio) + ".json", json.dumps(miss, indent=2))

    @after_this_request
    def cleanup(response):
        try:
            os.remove(zip_path)
        except Exception:
            pass
        return response

    fname = f"submission_{sub.id}_answers.zip"
    return send_file(zip_path, as_attachment=True, download_name=fname)



@tests_bp.route("/update_course_req/<int:req_id>", methods=["POST"])
@login_required
def update_course_req(req_id):
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    req = TestCourseRequirement.query.get_or_404(req_id)
    new_type = request.form.get("type")

    if new_type not in ("suggested", "recommended", "required"):
        return jsonify({"ok": False, "error": "Invalid requirement type"}), 400

    req.requirement_type = new_type
    db.session.commit()
    
    return jsonify({"ok": True, "message": f"Requirement updated to '{new_type.capitalize()}'."})
