from flask import render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from . import coaching_bp
from ..extensions import db
from .models import CoachingPlan, CoachingAcknowledge, CoachingPlanItem, CoachingPlanTest, CoachingPlanCourse
from ..teams.models import TeamMember, Team
from ..library.models import LibraryItem, LibraryView, LibraryRating, QuizAttempt
from ..utils.rbac import role_required
from sqlalchemy import func
from datetime import datetime
from ..models import User
from ..activity.models import LibrarySession
from ..notifications.utils import notify_team
from app.utils.datetime_tools import convert_for_render
from ..academy.models import AcademyCourse, AcademyCourseItem




# -------------------- [START] Add required imports --------------------
from sqlalchemy import func, or_
from datetime import datetime
from ..models import User
from ..activity.models import LibrarySession
# -------------------- [END] Add required imports --------------------


@coaching_bp.route("/create", methods=["GET", "POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def create_plan():
    from ..tests.models import TestCourseRequirement
    from ..library.models import LibraryItem
    from ..tests.models import Test
    from ..teams.models import Team
    from ..models import User

    if request.method == "POST":
        title = request.form["title"]
        desc = request.form.get("description")
        due = request.form.get("due_date")
        pass_percent = request.form.get("pass_percent", 60)
        assigned_to = request.form.get("assigned_to")
        # team_id = request.form.get("team_id")

        user_ids = [int(uid) for uid in request.form.getlist("user_ids") if uid.isdigit()]
        team_ids = [int(tid) for tid in request.form.getlist("team_ids") if tid.isdigit()]

        item_ids = request.form.getlist("items")
        test_ids = request.form.getlist("tests")


        # in create_plan() after you've created and flushed plan
        course_ids = request.form.getlist("academy_courses")

        # db.session.commit()


        from datetime import datetime, timedelta
        due_str = request.form.get("due_date")
        if due_str:  # user provided a date string
            try:
                due_date = datetime.strptime(due_str, "%Y-%m-%d")
            except ValueError:
                flash("Invalid due date format. Please use YYYY-MM-DD.", "danger")
                return redirect(request.url)
        else:  # default if form field is blank
            due_date = datetime.utcnow() + timedelta(days=7)  # default 7 days

        plan = CoachingPlan(
            title=title,
            description=desc,
            created_by=current_user.id,
            due_date=due_date,
            pass_percent=int(pass_percent),
        )



        # if assigned_to:
        #    plan.assigned_to = int(assigned_to)
        # if team_id:
        #    plan.team_id = int(team_id)

        # --- [FIX 2] Assign users/teams (from previous step) ---
        if user_ids:
            plan.assigned_users = User.query.filter(User.id.in_(user_ids)).all()
        if team_ids:
            plan.assigned_teams = Team.query.filter(Team.id.in_(team_ids)).all()

        db.session.add(plan)
        db.session.flush()

        for cid in course_ids:
            if cid.strip():
                add_academy_course_to_plan(plan, int(cid))

        # add selected library items
        for iid in item_ids:
            if not iid.strip():  # skip empty strings
                continue
            db.session.add(CoachingPlanItem(plan_id=plan.id, item_id=int(iid)))

        # add selected tests (and related courses)
        for tid in test_ids:
            if not tid.strip():
                continue
            t_id = int(tid)
            db.session.add(CoachingPlanTest(plan_id=plan.id, test_id=t_id))
            related = TestCourseRequirement.query.filter_by(test_id=t_id).all()
            for r in related:
                if not CoachingPlanItem.query.filter_by(plan_id=plan.id, item_id=r.course_id).first():
                    db.session.add(CoachingPlanItem(plan_id=plan.id, item_id=r.course_id))

        db.session.commit()

        if plan.assigned_to:
            from ..notifications.utils import create_notification
            create_notification(plan.assigned_to,
                f"🎯 You have been assigned a new coaching plan: {plan.title}")
        elif plan.team_id:
            from ..notifications.utils import notify_team
            notify_team(plan.team_id,
                f"📋 New coaching plan '{plan.title}' available for your team.")

        flash("Coaching plan created successfully.", "success")
        return redirect(url_for("coaching.list_plans"))

    # 👉 This handles the GET request
    teams = Team.query.all()
    items = LibraryItem.query.all()
    tests = Test.query.all()
    users = User.query.all()
    return render_template("coaching/create.html", teams=teams, items=items, tests=tests, users=users)




@coaching_bp.route("/")
@login_required
def list_plans():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 12, type=int)

    if current_user.role=="AGENT":
        team_ids=[tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id).all()]
        base = CoachingPlan.query.filter((CoachingPlan.assigned_to==current_user.id)|
                                         (CoachingPlan.team_id.in_(team_ids)))
    else:
        base = CoachingPlan.query
    pagination = base.order_by(CoachingPlan.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template("coaching/list.html", plans=pagination.items, pagination=pagination, per_page=per_page)



# --- [NEW] Route for agents to view a plan's details ---
@coaching_bp.route("/<int:plan_id>/view")
@login_required
def view_plan(plan_id):
    from ..library.models import LibraryView
    from ..tests.models import TestSubmission, Test

    plan = CoachingPlan.query.get_or_404(plan_id)
    user = current_user

    # --- Permission Check ---
    is_assigned = plan.assigned_to == user.id
    is_in_team = False
    if plan.team_id:
        is_in_team = any(tm.team_id == plan.team_id for tm in user.team_memberships)
    
    if not is_assigned and not is_in_team and user.role not in ("MANAGER", "ADMIN", "SUPER_ADMIN"):
        flash("You are not assigned to this coaching plan.", "danger")
        return redirect(url_for("coaching.list_plans"))

    # --- Gather Library Items and check completion ---
    plan_items = []
    for plan_item_link in plan.coaching_plan_items:
        item = plan_item_link.item
        if item:
            viewed = LibraryView.query.filter_by(user_id=user.id, item_id=item.id).first() is not None
            plan_items.append({"item": item, "viewed": viewed})

    # --- Gather Tests and check completion ---
    plan_tests = []
    for plan_test_link in plan.coaching_plan_tests:
        test = plan_test_link.test
        if test:
            submission = TestSubmission.query.filter_by(test_id=test.id, user_id=user.id).order_by(TestSubmission.submitted_at.desc()).first()
            passed = False
            if submission:
                total_q = len(test.questions)
                total_possible = total_q * 5 or 1
                percent = (submission.score or 0) / total_possible * 100
                if percent >= plan.pass_percent:
                    passed = True
            plan_tests.append({"test": test, "passed": passed, "submission": submission})
    
    # Check if already acknowledged
    acknowledged = CoachingAcknowledge.query.filter_by(plan_id=plan.id, user_id=user.id).first()

    return render_template("coaching/view.html", 
                           plan=plan, 
                           plan_items=plan_items, 
                           plan_tests=plan_tests,
                           acknowledged=acknowledged)




@coaching_bp.route("/acknowledge/<int:plan_id>", methods=["POST"])
@login_required
def acknowledge(plan_id):
    from ..library.models import LibraryView
    from ..tests.models import TestSubmission

    plan = CoachingPlan.query.get_or_404(plan_id)
    user = current_user

    # gather items and tests
    items = [ci.item_id for ci in CoachingPlanItem.query.filter_by(plan_id=plan.id)]
    tests = [ct.test_id for ct in CoachingPlanTest.query.filter_by(plan_id=plan.id)]

    # --- verify courses viewed ---
    viewed_items = [v.item_id for v in LibraryView.query.filter_by(user_id=user.id).all()]
    all_courses_ok = all(i in viewed_items for i in items) if items else True

    # --- verify tests taken and passed ---
    all_tests_ok = True
    if tests:
        for t_id in tests:
            s = (TestSubmission.query
                    .filter_by(test_id=t_id, user_id=user.id)
                    .order_by(TestSubmission.submitted_at.desc())
                    .first())
            if not s: all_tests_ok = False; break
            total_q=len(s.test.questions)
            total_possible=total_q*5 or 1
            percent=(s.score or 0)/total_possible*100
            if percent < plan.pass_percent:
                all_tests_ok=False; break

    if not (all_courses_ok and all_tests_ok):
        flash("☑️ Please complete all required courses and pass the tests before acknowledging.","danger")
        return redirect(url_for("coaching.list_plans"))

    # record acknowledgement
    existing = CoachingAcknowledge.query.filter_by(plan_id=plan.id, user_id=user.id).first()
    if not existing:
        db.session.add(CoachingAcknowledge(plan_id=plan.id, user_id=user.id))
        db.session.commit()


        # Notify all managers of teams this agent belongs to
        from ..teams.models import TeamMember
        for tm in TeamMember.query.filter_by(user_id=current_user.id).all():
            notify_team(tm.team_id, f"👏 {current_user.username} completed a coaching plan.")


        flash("✅ Coaching plan acknowledged successfully.","success")
    else:
        flash("Already acknowledged.","info")
    return redirect(url_for("coaching.list_plans"))




# --- [NEW] Route for managers to re-open a plan ---
@coaching_bp.route("/<int:plan_id>/unacknowledge/<int:user_id>", methods=["POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def unacknowledge(plan_id, user_id):
    ack = CoachingAcknowledge.query.filter_by(plan_id=plan_id, user_id=user_id).first()
    if ack:
        db.session.delete(ack)
        db.session.commit()
        
        # Notify the user that the plan was re-opened
        from ..notifications.utils import create_notification
        plan = CoachingPlan.query.get(plan_id)
        create_notification(user_id, f"Coaching plan '{plan.title}' has been re-opened by your manager.")
        
        flash("Plan re-opened for the user.", "success")
    else:
        flash("User had not acknowledged this plan.", "info")
    
    return redirect(url_for('coaching.plan_progress', plan_id=plan_id))






# Analytics with link to library
@coaching_bp.route("/analytics")
@login_required
def analytics():
    if current_user.role not in ("MANAGER","ADMIN","SUPER_ADMIN"):
        return redirect(url_for("dashboard.index"))
    team_ids=None
    if current_user.role=="MANAGER":
        team_ids=[tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id).all()]
    plans=CoachingPlan.query.all() if not team_ids else CoachingPlan.query.filter(CoachingPlan.team_id.in_(team_ids)).all()
    report=[]; labels=[]; completion_rates=[]; view_changes=[]; score_changes=[]; rating_changes=[]
    for p in plans:
        linked=CoachingPlanItem.query.filter_by(plan_id=p.id).all()
        linked_ids=[li.item_id for li in linked]
        views_before=views_after=0; ratings_before=ratings_after=0; rcount_b=rcount_a=0; scores_before=[]; scores_after=[]
        for iid in linked_ids:
            v=LibraryView.query.filter_by(item_id=iid).all()
            views_before+=len([x for x in v if x.viewed_at<p.created_at])
            views_after+=len([x for x in v if x.viewed_at>=p.created_at])
            r=LibraryRating.query.filter_by(item_id=iid).all()
            rb=[x.overall for x in r if x.created_at<p.created_at]; ra=[x.overall for x in r if x.created_at>=p.created_at]
            if rb: ratings_before+=sum(rb); rcount_b+=len(rb)
            if ra: ratings_after+=sum(ra); rcount_a+=len(ra)
            q=QuizAttempt.query.filter_by(item_id=iid).all()
            scores_before+=[x.score for x in q if x.created_at<p.created_at]
            scores_after+=[x.score for x in q if x.created_at>=p.created_at]
        avg_rating_b=ratings_before/rcount_b if rcount_b else 0
        avg_rating_a=ratings_after/rcount_a if rcount_a else 0
        avg_score_b=sum(scores_before)/len(scores_before) if scores_before else 0
        avg_score_a=sum(scores_after)/len(scores_after) if scores_after else 0
        view_delta=views_after-views_before
        completion=len([a for a in p.acknowledgments])
        completion_rate=(completion/1*100) if p.assigned_to else 0  # simplified
        report.append({"title":p.title,"completion":completion_rate,
                       "views_before":views_before,"views_after":views_after,
                       "avg_rating_b":avg_rating_b,"avg_rating_a":avg_rating_a,
                       "avg_score_b":avg_score_b,"avg_score_a":avg_score_a})
        labels.append(p.title); completion_rates.append(completion_rate)
        view_changes.append(view_delta); score_changes.append(avg_score_a-avg_score_b)
        rating_changes.append(avg_rating_a-avg_rating_b)
    chart_data={"labels":labels,"completion":completion_rates,"views":view_changes,"scores":score_changes,"ratings":rating_changes}
    return render_template("coaching/analytics.html",report=report,chart_data=chart_data)

# Agent profile with timeline and markers
@coaching_bp.route("/agent/<int:user_id>")
@login_required
def agent_profile(user_id):
    from ..models import User
    agent=User.query.get_or_404(user_id)
    if current_user.role=="MANAGER":
        my_team_ids=[tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id).all()]
        their_teams=[tm.team_id for tm in TeamMember.query.filter_by(user_id=agent.id).all()]
        if not any(t in my_team_ids for t in their_teams): abort(403)
    plans=CoachingPlan.query.filter((CoachingPlan.assigned_to==agent.id)|
                                    (CoachingPlan.team_id.in_([tm.team_id for tm in TeamMember.query.filter_by(user_id=agent.id).all()]))).all()
    profile_data=[]; plan_markers=[]
    for p in plans:
        plan_markers.append({"title":p.title,"date":p.created_at.date().isoformat()})
        linked_ids=[cpi.item_id for cpi in CoachingPlanItem.query.filter_by(plan_id=p.id).all()]
        ack=CoachingAcknowledge.query.filter_by(plan_id=p.id,user_id=agent.id).first()
        acked=ack.acknowledged_at if ack else None
        views=LibraryView.query.filter(LibraryView.user_id==agent.id,LibraryView.item_id.in_(linked_ids)).count()
        ratings=LibraryRating.query.filter(LibraryRating.user_id==agent.id,LibraryRating.item_id.in_(linked_ids)).all()
        quizzes=QuizAttempt.query.filter(QuizAttempt.user_id==agent.id,QuizAttempt.item_id.in_(linked_ids)).all()
        avg_rating=sum([r.overall for r in ratings])/len(ratings) if ratings else 0
        avg_score=sum([q.score for q in quizzes])/len(quizzes) if quizzes else 0
        profile_data.append({"plan":p.title,"due":p.due_date,"ack":acked,
                             "views":views,"avg_rating":avg_rating,"avg_score":avg_score})
    quiz_timeline=db.session.query(func.date(QuizAttempt.created_at),func.avg(QuizAttempt.score)).filter(QuizAttempt.user_id==agent.id).group_by(func.date(QuizAttempt.created_at)).all()
    rating_timeline=db.session.query(func.date(LibraryRating.created_at),func.avg(LibraryRating.overall)).filter(LibraryRating.user_id==agent.id).group_by(func.date(LibraryRating.created_at)).all()
    view_timeline=db.session.query(func.date(LibraryView.viewed_at),func.count(LibraryView.id)).filter(LibraryView.user_id==agent.id).group_by(func.date(LibraryView.viewed_at)).all()
    chart_data={"labels":[str(d[0]) for d in quiz_timeline],"quiz_scores":[float(d[1]) for d in quiz_timeline],
                "rating_labels":[str(d[0]) for d in rating_timeline],"ratings":[float(d[1]) for d in rating_timeline],
                "view_labels":[str(d[0]) for d in view_timeline],"views":[int(d[1]) for d in view_timeline],
                "markers":plan_markers}
    return render_template("coaching/agent_profile.html",agent=agent,profile_data=profile_data,chart_data=chart_data)




@coaching_bp.route("/<int:plan_id>/edit", methods=["GET","POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def edit_plan(plan_id):
    plan = CoachingPlan.query.get_or_404(plan_id)
    if request.method == "POST":
        plan.title = request.form["title"]
        plan.description = request.form.get("description")
        plan.due_date = datetime.strptime(request.form["due_date"],"%Y-%m-%d") if request.form.get("due_date") else None
        plan.pass_percent = int(request.form.get("pass_percent",60))

        # --- [FIX] Handle multiple assignments on EDIT ---
        user_ids = [int(uid) for uid in request.form.getlist("user_ids") if uid.isdigit()]
        team_ids = [int(tid) for tid in request.form.getlist("team_ids") if tid.isdigit()]

        plan.assigned_users = User.query.filter(User.id.in_(user_ids)).all()
        plan.assigned_teams = Team.query.filter(Team.id.in_(team_ids)).all()


        # --- [FIX] Clear existing requirements before adding new ones ---
        CoachingPlanItem.query.filter_by(plan_id=plan.id).delete()
        CoachingPlanTest.query.filter_by(plan_id=plan.id).delete()
        CoachingPlanCourse.query.filter_by(plan_id=plan.id).delete()
        
        # Re-add selected library items
        item_ids = request.form.getlist("items")
        for iid in item_ids:
            if iid.strip():
                db.session.add(CoachingPlanItem(plan_id=plan.id, item_id=int(iid)))

        # Re-add selected tests
        test_ids = request.form.getlist("tests")
        for tid in test_ids:
            if tid.strip():
                db.session.add(CoachingPlanTest(plan_id=plan.id, test_id=int(tid)))

        # Re-add selected academy courses and expand their contents
        course_ids = request.form.getlist("academy_courses")
        for cid in course_ids:
            if cid.strip():
                add_academy_course_to_plan(plan, int(cid))

        # After making changes, un-acknowledge the plan for all users so they must review it again.
        CoachingAcknowledge.query.filter_by(plan_id=plan.id).delete()

        db.session.commit()
        flash("Coaching plan updated. All users must re-acknowledge the changes.","success")
        return redirect(url_for("coaching.list_plans"))

    return render_template("coaching/edit.html", plan=plan, teams=Team.query.all())



@coaching_bp.route("/<int:plan_id>/delete", methods=["POST"])
@login_required
@role_required("MANAGER","ADMIN","SUPER_ADMIN")
def delete_plan(plan_id):
    plan = CoachingPlan.query.get_or_404(plan_id)
    db.session.delete(plan)
    db.session.commit()
    flash("Coaching plan deleted.","warning")
    return redirect(url_for("coaching.list_plans"))




@coaching_bp.route("/search_courses")
@login_required
def search_courses():
    q = request.args.get("q", "").strip()
    from ..library.models import LibraryItem
    results = []
    if q:
        results = LibraryItem.query.filter(
            LibraryItem.title.ilike(f"%{q}%")
        ).limit(10).all()
    return jsonify([{"id": item.id, "title": item.title} for item in results])

@coaching_bp.route("/search_tests")
@login_required
def search_tests():
    q = request.args.get("q", "").strip()
    from ..tests.models import Test
    results = []
    if q:
        results = Test.query.filter(
            Test.title.ilike(f"%{q}%")
        ).limit(10).all()
    return jsonify([{"id": t.id, "title": t.title} for t in results])



@coaching_bp.route("/progress/<int:plan_id>")
@login_required
def plan_progress(plan_id):
    plan = CoachingPlan.query.get_or_404(plan_id)
    from ..library.models import LibraryView
    from ..tests.models import TestSubmission

    if current_user.role == "MANAGER" or current_user.role == "SUPER_ADMIN" or current_user.role == "ADMIN":
        team_ids = [tm.team_id for tm in TeamMember.query.filter_by(user_id=current_user.id).all()]
        members = User.query.join(TeamMember, TeamMember.user_id == User.id)\
                            .filter(TeamMember.team_id.in_(team_ids)).all()
    else:
        members = [current_user]

    report = []
    for u in members:
        # Courses viewed
        items = [c.item_id for c in CoachingPlanItem.query.filter_by(plan_id=plan.id)]
        viewed = [v.item_id for v in LibraryView.query.filter_by(user_id=u.id).all()]
        course_done = sum(1 for i in items if i in viewed)
        total_courses = len(items)

        # Tests passed
        tests = [ct.test_id for ct in CoachingPlanTest.query.filter_by(plan_id=plan.id)]
        passed = 0
        for t in tests:
            s = TestSubmission.query.filter_by(test_id=t, user_id=u.id)\
                .order_by(TestSubmission.submitted_at.desc()).first()
            if not s:
                continue
            total_q = len(s.test.questions)
            total_possible = total_q * 5
            percent = (s.score or 0) / total_possible * 100 if total_possible else 0
            if percent >= plan.pass_percent:
                passed += 1
        report.append({
            "user_id": u.id,
            "username": u.username,
            "courses": f"{course_done}/{total_courses}",
            "tests": f"{passed}/{len(tests)}",
            "ack": bool(CoachingAcknowledge.query.filter_by(plan_id=plan.id, user_id=u.id).first())
        })

    return render_template("coaching/progress.html", plan=plan, report=report)




@coaching_bp.route("/progress/<int:plan_id>/user/<int:user_id>")
@login_required
def user_progress(plan_id, user_id):
    """Detailed per‑user breakdown for a plan."""
    from ..library.models import LibraryItem, LibraryView
    from ..tests.models import Test, TestSubmission
    from ..models import User

    plan = CoachingPlan.query.get_or_404(plan_id)
    user = User.query.get_or_404(user_id)

    # --- gather courses ---
    course_entries = []
    items = [
        (ci.item_id, LibraryItem.query.get(ci.item_id))
        for ci in CoachingPlanItem.query.filter_by(plan_id=plan.id)
    ]
    for iid, item in items:
        viewed = LibraryView.query.filter_by(item_id=iid, user_id=user.id).first() is not None
        course_entries.append({
            "title": item.title if item else "(deleted)",
            "viewed": viewed
        })

    # --- gather tests and last submission stats ---
    test_entries = []
    tests = [
        (ct.test_id, Test.query.get(ct.test_id))
        for ct in CoachingPlanTest.query.filter_by(plan_id=plan.id)
    ]
    for tid, test in tests:
        sub = (
            TestSubmission.query.filter_by(test_id=tid, user_id=user.id)
            .order_by(TestSubmission.submitted_at.desc())
            .first()
        )
        if sub and test:
            total_q = len(test.questions)
            total_possible = total_q * 5 or 1
            percent = (sub.score or 0) / total_possible * 100
            test_entries.append({
                "title": test.title,
                "score": f"{percent:.1f} %",
                "submitted_at": sub.submitted_at.strftime("%Y-%m-%d %H:%M")
            })
        elif test:
            test_entries.append({
                "title": test.title,
                "score": "— not taken —",
                "submitted_at": ""
            })
    # Convert all datetimes to the user’s tz, format them
    # test_entries = convert_for_render(test_entries, fmt="%Y-%m-%d %H:%M")
    return render_template(
        "coaching/user_progress.html",
        plan=plan,
        user=user,
        courses=course_entries,
        tests=test_entries
    )






# -------------------- [START] New route for user search --------------------
@coaching_bp.route("/search_users")
@login_required
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
# -------------------- [END] New route for user search --------------------



# -------------------- [START] New route to duplicate a plan --------------------
@coaching_bp.route("/<int:plan_id>/duplicate", methods=["POST"])
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def duplicate_plan(plan_id):
    original_plan = CoachingPlan.query.get_or_404(plan_id)

    # Create the new plan, adding "(Copy)" to the title
    new_plan = CoachingPlan(
        title=original_plan.title + " (Copy)",
        description=original_plan.description,
        created_by=current_user.id, # Set current user as creator
        due_date=original_plan.due_date,
        pass_percent=original_plan.pass_percent,
        assigned_to=original_plan.assigned_to,
        team_id=original_plan.team_id
    )
    db.session.add(new_plan)
    db.session.flush() # Flush to get the new_plan.id

    # Copy all associated library items
    for item_link in original_plan.coaching_plan_items:
        new_item_link = CoachingPlanItem(
            plan_id=new_plan.id,
            item_id=item_link.item_id
        )
        db.session.add(new_item_link)

    # Copy all associated tests
    for test_link in original_plan.coaching_plan_tests:
        new_test_link = CoachingPlanTest(
            plan_id=new_plan.id,
            test_id=test_link.test_id
        )
        db.session.add(new_test_link)

    # in duplicate_plan()
    from .models import CoachingPlanCourse
    for course_link in original_plan.coaching_plan_courses:
        db.session.add(CoachingPlanCourse(plan_id=new_plan.id, course_id=course_link.course_id))
    

    db.session.commit()
    flash(f"Successfully duplicated plan '{original_plan.title}'.", "success")
    return redirect(url_for("coaching.list_plans"))
# -------------------- [END] New route to duplicate a plan --------------------



def add_academy_course_to_plan(plan, course_id: int):
    # Link the course to the plan (idempotent)
    from .models import CoachingPlanCourse, CoachingPlanItem, CoachingPlanTest
    link = CoachingPlanCourse.query.filter_by(plan_id=plan.id, course_id=course_id).first()
    if not link:
        db.session.add(CoachingPlanCourse(plan_id=plan.id, course_id=course_id))

    course = AcademyCourse.query.get(course_id)
    if not course:
        return

    # Expand modules into plan items/tests (skip duplicates)
    for mod in course.items or []:
        if mod.type == "library" and mod.library_item_id:
            exists = CoachingPlanItem.query.filter_by(
                plan_id=plan.id, item_id=mod.library_item_id
            ).first()
            if not exists:
                db.session.add(CoachingPlanItem(plan_id=plan.id, item_id=mod.library_item_id))
        elif mod.type == "test" and mod.test_id:
            exists = CoachingPlanTest.query.filter_by(
                plan_id=plan.id, test_id=mod.test_id
            ).first()
            if not exists:
                db.session.add(CoachingPlanTest(plan_id=plan.id, test_id=mod.test_id))


@coaching_bp.route("/search_academy_courses")
@login_required
@role_required("MANAGER", "ADMIN", "SUPER_ADMIN")
def search_academy_courses():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    courses = (AcademyCourse.query
               .filter(AcademyCourse.published == True,
                       AcademyCourse.title.ilike(like))
               .order_by(AcademyCourse.title.asc())
               .limit(15).all())
    return jsonify([{"id": c.id, "title": c.title} for c in courses])



