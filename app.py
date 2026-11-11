from __future__ import annotations

import logging
import os
import secrets
import string
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import wraps

import joblib
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import false, inspect, text
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

STATUSES = ["Submitted", "Under Review", "In Progress", "Resolved", "Rejected"]
CATEGORIES = ["Academics", "Canteen", "Hostel", "Library", "Transport", "Others"]
DEPARTMENT_EMAIL_ENV = {
    "Academics": "DEPARTMENT_EMAIL_ACADEMICS",
    "Canteen": "DEPARTMENT_EMAIL_CANTEEN",
    "Hostel": "DEPARTMENT_EMAIL_HOSTEL",
    "Library": "DEPARTMENT_EMAIL_LIBRARY",
    "Transport": "DEPARTMENT_EMAIL_TRANSPORT",
    "Others": "DEPARTMENT_EMAIL_OTHERS",
}


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="student")
    department = db.Column(db.String(40), nullable=True)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    complaints = db.relationship("Complaint", backref="submitter", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking_id = db.Column(db.String(20), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    complaint_text = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(40), nullable=False, index=True)
    priority = db.Column(db.String(20), nullable=False, default="Normal", index=True)
    status = db.Column(db.String(30), nullable=False, default="Submitted", index=True)
    admin_note = db.Column(db.Text)
    model_confidence = db.Column(db.Float, nullable=True)
    possible_duplicate_of = db.Column(db.Integer, db.ForeignKey("complaint.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    history = db.relationship("StatusHistory", backref="complaint", cascade="all, delete-orphan", order_by="StatusHistory.created_at")
    feedback = db.relationship("Feedback", backref="complaint", uselist=False, cascade="all, delete-orphan")

class StatusHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("complaint.id"), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False)
    note = db.Column(db.String(500))
    changed_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(
        db.Integer,
        db.ForeignKey("complaint.id"),
        nullable=False,
        unique=True,
        index=True
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=utcnow,
        nullable=False
    )


def estimate_priority(text: str) -> str:
    """Transparent keyword heuristic; not an ML urgency model."""
    urgent_terms = ("fire", "unsafe", "harassment", "violence", "emergency", "injury", "electric shock", "threat")
    high_terms = ("no water", "water supply", "medical", "broken", "not working", "exposed wire", "ragging")
    normalized = text.lower()
    if any(term in normalized for term in urgent_terms):
        return "Urgent"
    if any(term in normalized for term in high_terms):
        return "High"
    return "Normal"


def make_tracking_id():
    alphabet = string.ascii_uppercase + string.digits
    while True:
        value = "CD-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if not Complaint.query.filter_by(tracking_id=value).first():
            return value


def send_department_email(category: str, complaint: Complaint) -> str:
    import smtplib
    from email.message import EmailMessage

    recipient_key = DEPARTMENT_EMAIL_ENV.get(category)
    recipient = os.getenv(recipient_key, "") if recipient_key else ""
    sender, password = os.getenv("SMTP_USERNAME", ""), os.getenv("SMTP_PASSWORD", "")
    if not all((recipient, sender, password)):
        return "not_configured"
    message = EmailMessage()
    message["Subject"] = f"New grievance {complaint.tracking_id} — {category}"
    message["From"], message["To"] = sender, recipient
    message.set_content(
        f"Tracking ID: {complaint.tracking_id}\nCategory: {category}\n"
        f"Priority: {complaint.priority}\nSubmitter: {complaint.submitter.name}\n\n"
        f"{complaint.complaint_text}"
    )
    try:
        with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465")), timeout=15) as smtp:
            smtp.login(sender, password)
            smtp.send_message(message)
        return "sent"
    except (OSError, smtplib.SMTPException):
        logging.getLogger(__name__).exception("Department email failed")
        return "failed"


def send_user_email(user_email: str, subject: str, body: str) -> str:
    import smtplib
    from email.message import EmailMessage
    sender, password = os.getenv("SMTP_USERNAME", ""), os.getenv("SMTP_PASSWORD", "")
    if not sender or not password or not user_email:
        return "not_configured"
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, sender, user_email
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465")), timeout=15) as smtp:
            smtp.login(sender, password)
            smtp.send_message(message)
        return "sent"
    except Exception:
        logging.getLogger(__name__).exception("User email notification failed")
        return "failed"


def make_token(email: str, purpose: str) -> str:
    return URLSafeTimedSerializer(app.config["SECRET_KEY"]).dumps(email, salt=f"civicdesk-{purpose}")


def find_duplicate(text_value: str, category: str):
    """Conservative lexical similarity check against recent unresolved grievances."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    candidates = Complaint.query.filter_by(category=category).filter(
        Complaint.status.in_(["Submitted", "Under Review", "In Progress"])
    ).order_by(Complaint.created_at.desc()).limit(100).all()
    if not candidates:
        return None
    corpus = [c.complaint_text for c in candidates] + [text_value]
    try:
        matrix = TfidfVectorizer(ngram_range=(1, 2), stop_words="english").fit_transform(corpus)
        scores = cosine_similarity(matrix[-1], matrix[:-1]).ravel()
        best_index = int(scores.argmax())
        return candidates[best_index] if float(scores[best_index]) >= 0.82 else None
    except ValueError:
        return None


def is_staff(user):
    return user.is_authenticated and user.role in ("admin", "department_admin")


def staff_complaints_query():
    """Return all complaints for global admins, or only the staff member's department."""
    query = Complaint.query
    if current_user.role == "department_admin":
        query = query.filter_by(category=current_user.department)
    return query


def staff_can_manage(complaint):
    return current_user.role == "admin" or (
        current_user.role == "department_admin" and complaint.category == current_user.department
    )


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not is_staff(current_user):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "unsafe-dev-key-change-before-deploy"),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", f"sqlite:///{Path(app.instance_path) / 'civicdesk.sqlite'}"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=32 * 1024,
        MAX_COMPLAINT_LENGTH=5000,
        MODEL_PATH=os.getenv("MODEL_PATH", str(ROOT / "models" / "grievance_pipeline.joblib")),
        REQUIRE_EMAIL_VERIFICATION=os.getenv("REQUIRE_EMAIL_VERIFICATION", "0") == "1",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"
    login_manager.login_message = "Please sign in to continue."
    csrf.init_app(app)

    model = None
    model_path = Path(app.config["MODEL_PATH"])
    if model_path.is_file():
        try:
            model = joblib.load(model_path)
        except Exception:
            app.logger.exception("Could not load classifier model")
    app.extensions["grievance_classifier"] = model

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.context_processor
    def inject_globals():
        return {"STATUSES": STATUSES, "CATEGORIES": CATEGORIES}

    @app.get("/")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return render_template("home.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if len(name) < 2 or len(name) > 100 or "@" not in email or len(password) < 8:
                flash("Enter a valid name and email. Password must be at least 8 characters.", "error")
            elif User.query.filter_by(email=email).first():
                flash("An account with that email already exists.", "error")
            else:
                user = User(name=name, email=email, role="student")
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                token = make_token(user.email, "verify")
                verify_url = url_for("verify_email", token=token, _external=True)
                send_user_email(user.email, "Verify your CivicDesk email", f"Verify your email using this link (valid 24 hours): {verify_url}")
                if app.config.get("REQUIRE_EMAIL_VERIFICATION"):
                    flash("Account created. Check your email to verify before signing in.", "success")
                    return redirect(url_for("login"))
                user.email_verified = True
                db.session.commit()
                login_user(user)
                flash("Your account is ready. You can now submit and track grievances.", "success")
                return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                if app.config.get("REQUIRE_EMAIL_VERIFICATION") and not user.email_verified:
                    flash("Please verify your email before signing in.", "error")
                    return redirect(url_for("login"))
                login_user(user)
                return redirect(url_for("admin_dashboard" if user.role in ("admin", "department_admin") else "dashboard"))
            flash("Invalid email or password.", "error")
        return render_template("auth.html", mode="login")

    @app.get("/verify-email/<token>")
    def verify_email(token):
        try:
            email = URLSafeTimedSerializer(app.config["SECRET_KEY"]).loads(token, salt="civicdesk-verify", max_age=86400)
        except (BadSignature, SignatureExpired):
            flash("Verification link is invalid or expired.", "error")
            return redirect(url_for("login"))
        user = User.query.filter_by(email=email).first()
        if not user:
            abort(404)
        user.email_verified = True
        db.session.commit()
        flash("Email verified. You can sign in.", "success")
        return redirect(url_for("login"))

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = User.query.filter_by(email=email).first()
            if user:
                token = make_token(user.email, "reset")
                reset_url = url_for("reset_password", token=token, _external=True)
                send_user_email(user.email, "CivicDesk password reset", f"Reset your password using this link (valid 1 hour): {reset_url}")
            flash("If that email is registered, a password reset link has been sent.", "success")
            return redirect(url_for("login"))
        return render_template("password.html", mode="forgot")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        try:
            email = URLSafeTimedSerializer(app.config["SECRET_KEY"]).loads(token, salt="civicdesk-reset", max_age=3600)
        except (BadSignature, SignatureExpired):
            flash("Password reset link is invalid or expired.", "error")
            return redirect(url_for("forgot_password"))
        user = User.query.filter_by(email=email).first_or_404()
        if request.method == "POST":
            password = request.form.get("password", "")
            if len(password) < 8:
                flash("Password must be at least 8 characters.", "error")
            else:
                user.set_password(password)
                db.session.commit()
                flash("Password updated. Please sign in.", "success")
                return redirect(url_for("login"))
        return render_template("password.html", mode="reset")

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been signed out.", "success")
        return redirect(url_for("home"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        if is_staff(current_user):
            return redirect(url_for("admin_dashboard"))
        complaints = Complaint.query.filter_by(user_id=current_user.id).order_by(Complaint.created_at.desc()).all()
        return render_template("student_dashboard.html", complaints=complaints)

    @app.route("/submit", methods=["GET", "POST"])
    @login_required
    def submit_complaint():
        if request.method == "POST":
            text = request.form.get("complaint", "").strip()
            if len(text) < 8 or len(text) > app.config["MAX_COMPLAINT_LENGTH"]:
                flash(f"Complaint must be between 8 and {app.config['MAX_COMPLAINT_LENGTH']} characters.", "error")
                return redirect(url_for("submit_complaint"))
            classifier = app.extensions.get("grievance_classifier")
            if classifier is None:
                flash("The ML model is missing. Follow the README training instructions first.", "error")
                return redirect(url_for("submit_complaint"))
            try:
                category = str(classifier.predict([text])[0])
                if category not in CATEGORIES:
                    category = "Others"
                probabilities = classifier.predict_proba([text])[0] if hasattr(classifier, "predict_proba") else []
                confidence = float(max(probabilities)) if len(probabilities) else None
                duplicate = find_duplicate(text, category)
            except Exception:
                app.logger.exception("Classification failed")
                flash("Could not classify this complaint. Please try again.", "error")
                return redirect(url_for("submit_complaint"))
            complaint = Complaint(
                tracking_id=make_tracking_id(), user_id=current_user.id,
                complaint_text=text, category=category, priority=estimate_priority(text),
                status="Submitted", model_confidence=confidence,
                possible_duplicate_of=duplicate.id if duplicate else None
            )
            db.session.add(complaint)
            db.session.flush()
            db.session.add(StatusHistory(complaint_id=complaint.id, status="Submitted",
                                         note="Grievance submitted.", changed_by=current_user.id))
            db.session.commit()
            email_status = send_department_email(category, complaint)
            send_user_email(current_user.email, f"CivicDesk grievance received: {complaint.tracking_id}",
                            f"Your grievance was saved. Tracking ID: {complaint.tracking_id}. Department: {category}.")
            if duplicate:
                flash(f"A similar open grievance may already exist ({duplicate.tracking_id}). Your submission was still saved.", "error")
            else:
                flash(f"Grievance saved. Your tracking ID is {complaint.tracking_id}.", "success")
            return render_template("submitted.html", complaint=complaint, email_status=email_status)
        return render_template("submit.html")

    @app.get("/complaints/<tracking_id>")
    @login_required
    def complaint_detail(tracking_id):
        complaint = Complaint.query.filter_by(tracking_id=tracking_id).first_or_404()
        if current_user.role == "student" and complaint.user_id != current_user.id:
            abort(403)
        if is_staff(current_user) and not staff_can_manage(complaint):
            abort(403)
        return render_template("complaint_detail.html", complaint=complaint)

    @app.get("/track")
    @login_required
    def track():
        tracking_id = request.args.get("tracking_id", "").strip().upper()
        if not tracking_id:
            return render_template("track.html", complaint=None)
        complaint = Complaint.query.filter_by(tracking_id=tracking_id).first()
        if complaint and current_user.role == "student" and complaint.user_id != current_user.id:
            complaint = None
        if complaint and is_staff(current_user) and not staff_can_manage(complaint):
            complaint = None
        if not complaint:
            flash("No grievance found for that tracking ID in your account.", "error")
        return render_template("track.html", complaint=complaint)

    @app.post("/complaints/<tracking_id>/feedback")
    @login_required
    def submit_feedback(tracking_id):
        complaint = Complaint.query.filter_by(tracking_id=tracking_id, user_id=current_user.id).first_or_404()
        if complaint.status != "Resolved":
            abort(403)
        rating = request.form.get("rating", type=int)
        comment = request.form.get("comment", "").strip()
        if rating not in range(1, 6) or len(comment) > 1000:
            flash("Choose a rating from 1 to 5 and keep comments under 1000 characters.", "error")
        elif Feedback.query.filter_by(complaint_id=complaint.id).first():
            flash("Feedback has already been submitted for this grievance.", "error")
        else:
            db.session.add(Feedback(complaint_id=complaint.id, user_id=current_user.id, rating=rating, comment=comment))
            db.session.commit()
            flash("Thank you for your feedback.", "success")
        return redirect(url_for("complaint_detail", tracking_id=tracking_id))

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        status_filter = request.args.get("status", "")
        category_filter = request.args.get("category", "")
        base_query = staff_complaints_query()
        allowed_categories = [current_user.department] if current_user.role == "department_admin" else CATEGORIES
        if status_filter in STATUSES:
            base_query = base_query.filter_by(status=status_filter)
        if category_filter in allowed_categories:
            base_query = base_query.filter_by(category=category_filter)
        elif category_filter and category_filter not in allowed_categories:
            base_query = base_query.filter(false())
        complaints = base_query.order_by(Complaint.created_at.desc()).limit(200).all()

        scoped = staff_complaints_query()
        total = scoped.count()
        resolved = scoped.filter_by(status="Resolved").count()
        pending = scoped.filter(Complaint.status.in_(["Submitted", "Under Review", "In Progress"])).count()
        category_counts = {c: staff_complaints_query().filter_by(category=c).count() for c in allowed_categories}
        status_counts = {st: staff_complaints_query().filter_by(status=st).count() for st in STATUSES}
        return render_template("admin_dashboard.html", complaints=complaints, total=total,
                               resolved=resolved, pending=pending, category_counts=category_counts,
                               status_counts=status_counts, status_filter=status_filter,
                               category_filter=category_filter, allowed_categories=allowed_categories)

    @app.post("/admin/complaints/<int:complaint_id>/update")
    @admin_required
    def update_complaint(complaint_id):
        complaint = db.get_or_404(Complaint, complaint_id)
        if not staff_can_manage(complaint):
            abort(403)
        status = request.form.get("status", "")
        note = request.form.get("note", "").strip()
        if status not in STATUSES:
            abort(400)
        if len(note) > 500:
            flash("Internal note must be 500 characters or fewer.", "error")
            return redirect(url_for("complaint_detail", tracking_id=complaint.tracking_id))
        if status != complaint.status or note:
            complaint.status = status
            complaint.admin_note = note or complaint.admin_note
            complaint.updated_at = utcnow()
            db.session.add(StatusHistory(complaint_id=complaint.id, status=status,
                                         note=note or f"Status changed to {status}.",
                                         changed_by=current_user.id))
            db.session.commit()
            send_user_email(complaint.submitter.email,
                f"CivicDesk update: {complaint.tracking_id} is {status}",
                f"Your grievance {complaint.tracking_id} status is now: {status}.\nDepartment: {complaint.category}.\nNote: {note or 'No additional note.'}")
            flash("Grievance updated successfully.", "success")
        return redirect(url_for("complaint_detail", tracking_id=complaint.tracking_id))

    @app.get("/admin/analytics")
    @admin_required
    def analytics():
        allowed_categories = [current_user.department] if current_user.role == "department_admin" else CATEGORIES
        scoped = staff_complaints_query()
        categories = {c: scoped.filter_by(category=c).count() for c in allowed_categories}
        statuses = {st: scoped.filter_by(status=st).count() for st in STATUSES}
        monthly = {}
        for item in scoped.all():
            key = item.created_at.strftime("%Y-%m")
            monthly[key] = monthly.get(key, 0) + 1
        feedback_rows = Feedback.query.join(Complaint).filter(Complaint.category.in_(allowed_categories)).all()
        avg_rating = round(sum(x.rating for x in feedback_rows) / len(feedback_rows), 2) if feedback_rows else None
        return render_template("analytics.html", categories=categories, statuses=statuses,
                               total=scoped.count(), monthly=monthly, avg_rating=avg_rating)

    @app.get("/health")
    def health():
        return jsonify(status="ok", model_loaded=app.extensions.get("grievance_classifier") is not None)

    @app.cli.command("init-db")
    def init_db_command():
        db.create_all()
        print("Database tables created.")

    def create_staff_account(role, department=None):
        name, email, password = (os.getenv(k, "").strip() for k in ("ADMIN_NAME", "ADMIN_EMAIL", "ADMIN_PASSWORD"))
        if not all((name, email, password)) or len(password) < 12:
            raise SystemExit("Set ADMIN_NAME, ADMIN_EMAIL and ADMIN_PASSWORD (12+ characters) in environment.")
        if role == "department_admin" and department not in CATEGORIES:
            raise SystemExit("Set ADMIN_DEPARTMENT to one of: " + ", ".join(CATEGORIES))
        if User.query.filter_by(email=email.lower()).first():
            raise SystemExit("An account with this email already exists.")
        user = User(name=name, email=email.lower(), role=role, department=department)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"{role.replace('_', ' ').title()} account created for {email}" +
              (f" ({department})." if department else "."))

    @app.cli.command("create-admin")
    def create_admin_command():
        """Create a global administrator account."""
        with app.app_context():
            create_staff_account("admin")

    @app.cli.command("create-department-admin")
    def create_department_admin_command():
        """Create a department-scoped administrator using ADMIN_DEPARTMENT."""
        with app.app_context():
            create_staff_account("department_admin", os.getenv("ADMIN_DEPARTMENT", "").strip())

    with app.app_context():
        db.create_all()
        # Lightweight SQLite-compatible migration for existing CivicDesk databases.
        # Existing student/admin accounts remain intact; old admins remain global admins.
        inspector = inspect(db.engine)
        user_columns = {column["name"] for column in inspector.get_columns("user")}
        with db.engine.begin() as connection:
            if "department" not in user_columns:
                connection.execute(text("ALTER TABLE user ADD COLUMN department VARCHAR(40)"))
            if "email_verified" not in user_columns:
                connection.execute(text("ALTER TABLE user ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 1"))
            complaint_columns = {c["name"] for c in inspect(db.engine).get_columns("complaint")}
            if "model_confidence" not in complaint_columns:
                connection.execute(text("ALTER TABLE complaint ADD COLUMN model_confidence FLOAT"))
            if "possible_duplicate_of" not in complaint_columns:
                connection.execute(text("ALTER TABLE complaint ADD COLUMN possible_duplicate_of INTEGER"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
