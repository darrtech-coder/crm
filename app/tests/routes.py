from flask import render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from . import tests_bp
from ..extensions import db
from .models import Test, TestQuestion, TestOption, TestSubmission, TestAnswer, TestPrerequisite
from datetime import datetime
from werkzeug.utils import secure_filename
import os, json

# up_dir = os.path.join(current_app.root_path, "..", "uploads", "audio_answers")

# Manager/Admin create tests
@tests_bp.route("/")
@login_required
def index():
    if current_user.role in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        tests = Test.query.all()
    else:
        tests = Test.query.all()  # Later: filter assigned tests only
    return render_template("tests/index.html", tests=tests)


@tests_bp.route("/create", methods=["GET", "POST"])
@login_required
def create():
    if current_user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        flash("Unauthorized", "danger")
        return redirect(url_for("tests.index"))

    if request.method == "POST":
        title = request.form["title"]
        desc = request.form.get("description")
        t = Test(title=title, description=desc, created_by=current_user.id)
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
        test.status=request.form.get("status")
        pub=request.form.get("publish_at")
        test.publish_at=datetime.fromisoformat(pub) if pub else None
        db.session.commit()
        flash("Updated", "success")
    return render_template("tests/edit_inline.html", test=test, library_items=LibraryItem.query.all())





@tests_bp.route("/<int:test_id>/take", methods=["GET", "POST"])
@login_required
def take(test_id):
    from ..library.models import LibraryView
    import json

    test = Test.query.get_or_404(test_id)

    # --- Course requirement checks ---
    requirements = TestCourseRequirement.query.filter_by(test_id=test.id).all()
    unmet_required, unmet_recommended = [], []

    for r in requirements:
        done = LibraryView.query.filter_by(
            user_id=current_user.id, item_id=r.course_id
        ).first() is not None
        if r.requirement_type == "required" and not done:
            unmet_required.append(r.course)
        elif r.requirement_type == "recommended" and not done:
            unmet_recommended.append(r.course)

    # --- Block or warn before start ---
    if unmet_required:
        flash("You must complete the required courses before this test.", "danger")
        return render_template("tests/requirements_block.html", required=unmet_required)

    # --- POST: final submission ---
    if request.method == "POST":
        submission = TestSubmission(
            test_id=test.id,
            user_id=current_user.id,
            submitted_at=datetime.utcnow()
        )
        db.session.add(submission)
        db.session.flush()

        # --- handle audio blobs in hidden fields ---
        for key, val in request.form.items():
            if key.startswith("recorded_audio_") and val:
                import base64, uuid
                fname = f"{uuid.uuid4().hex}.webm"
                dest_dir = os.path.join(
                    current_app.root_path, "..", "uploads", "media", "audio_answers"
                )
                os.makedirs(dest_dir, exist_ok=True)
                with open(os.path.join(dest_dir, fname), "wb") as f:
                    f.write(base64.b64decode(val))

        # --- iterate questions and save answers ---
        for q in test.questions:
            qid = str(q.id)
            if q.type == "mcq":
                opt_id = request.form.get(f"q{qid}")
                if opt_id:
                    option = TestOption.query.get(int(opt_id))
                    db.session.add(
                        TestAnswer(
                            submission_id=submission.id,
                            question_id=q.id,
                            selected_option=option.id,
                            is_correct=option.is_correct,
                        )
                    )
            elif q.type == "short_text":
                txt = request.form.get(f"q{qid}")
                db.session.add(
                    TestAnswer(
                        submission_id=submission.id,
                        question_id=q.id,
                        answer_text=txt,
                    )
                )
            elif q.type == "audio":
                uploaded = request.files.get(f"q{q.id}")
                fname = None
                if uploaded and uploaded.filename:
                    fname = secure_filename(uploaded.filename)
                    target = os.path.join(
                        current_app.root_path, "..", "uploads", "media", "audio_answers"
                    )
                    os.makedirs(target, exist_ok=True)
                    uploaded.save(os.path.join(target, fname))
                db.session.add(
                    TestAnswer(
                        submission_id=submission.id,
                        question_id=q.id,
                        answer_audio=fname,
                        score=0.0,
                    )
                )

        # --- auto‑grade MCQs ---
        total_score = 0.0
        for ans in submission.answers:
            if ans.question.type == "mcq":
                ans.score = 5.0 if ans.is_correct else 0.0
            total_score += ans.score or 0.0
        submission.score = round(total_score, 1)
        db.session.commit()

        # --- summary stats ---
        mcq_answers = [a for a in submission.answers if a.question.type == "mcq"]
        non_mcq_answers = [a for a in submission.answers if a.question.type != "mcq"]
        min_mcq = sum(a.score or 0 for a in mcq_answers)
        max_mcq = min_mcq + (len(non_mcq_answers) * 5.0)
        total_possible = len(test.questions) * 5.0
        total_questions = len(test.questions)
        avg_score = db.session.query(db.func.avg(TestSubmission.score)).filter_by(
            test_id=test.id
        ).scalar()

        # Clean up autosave draft after submit
        redis_key = f"autosave:{test.id}:{current_user.id}"
        try:
            current_app.redis.delete(redis_key)
        except Exception:
            pass

        return render_template(
            "tests/post_summary.html",
            test=test,
            submission=submission,
            min_mcq=min_mcq,
            max_mcq=max_mcq,
            total_possible=total_possible,
            total_questions=total_questions,
            avg_score=avg_score,
        )

    # --------------------------------------------------------------
    # --- Default GET: display test page and restore autosaved draft
    # --------------------------------------------------------------
    if unmet_recommended:
        flash("You have recommended courses still incomplete.", "warning")

    redis_key = f"autosave:{test.id}:{current_user.id}"
    autosaved = {}
    try:
        cached = current_app.redis.get(redis_key)
    except Exception:
        cached = None

    if cached:
        try:
            autosaved = json.loads(cached).get("answers", {})
            flash("Restored your last autosave — continue where you left off.", "info")
            return render_template("tests/take.html", test=test, autosaved=autosaved)
        except Exception:
            pass

    # --- No autosave found or failed to load
    return render_template("tests/take.html", test=test, autosaved={})




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

    key = f"autosave:{test_id}:{current_user.id}"
    payload = json.dumps({
        "answers": answers,
        "saved_at": datetime.utcnow().isoformat()
    })

    # store in Redis for 2 hours (7200 s)
    current_app.redis.setex(key, 7200, payload)
    return jsonify({"ok": True, "saved": True})



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

