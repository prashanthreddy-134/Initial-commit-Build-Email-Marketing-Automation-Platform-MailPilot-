import csv
import io
import os
import re
import secrets

from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse, urljoin

from flask import (
    Flask, render_template, redirect, url_for, flash, request,
    Response, abort, current_app
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import func


db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()


def now():
    return datetime.utcnow()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Subscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    first_name = db.Column(db.String(80), default="")
    last_name = db.Column(db.String(80), default="")
    company = db.Column(db.String(120), default="")
    source = db.Column(db.String(40), default="manual")
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    consent_at = db.Column(db.DateTime, default=now)
    unsubscribed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now)


class Segment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=now)


class SegmentMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    segment_id = db.Column(db.Integer, db.ForeignKey("segment.id"), nullable=False)
    subscriber_id = db.Column(db.Integer, db.ForeignKey("subscriber.id"), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("segment_id", "subscriber_id"),
    )


class EmailTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now)


class Campaign(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="draft", nullable=False)
    scheduled_at = db.Column(db.DateTime)
    sent_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    segment_id = db.Column(db.Integer, db.ForeignKey("segment.id"))
    created_at = db.Column(db.DateTime, default=now)

    recipients = db.relationship(
        "CampaignRecipient",
        backref="campaign",
        cascade="all, delete-orphan"
    )


class CampaignRecipient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaign.id"), nullable=False)
    subscriber_id = db.Column(db.Integer, db.ForeignKey("subscriber.id"), nullable=False)
    sent_at = db.Column(db.DateTime, default=now)
    opened_at = db.Column(db.DateTime)
    clicked_at = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint("campaign_id", "subscriber_id"),
    )


class AutomationSequence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    delay_days = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now)


class SequenceEnrollment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sequence_id = db.Column(
        db.Integer,
        db.ForeignKey("automation_sequence.id"),
        nullable=False
    )
    subscriber_id = db.Column(
        db.Integer,
        db.ForeignKey("subscriber.id"),
        nullable=False
    )
    due_at = db.Column(db.DateTime, nullable=False)
    sent_at = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint("sequence_id", "subscriber_id"),
    )


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(120), nullable=False)
    detail = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=now)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def valid_email(value):
    return bool(
        re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            (value or "").strip()
        )
    )


def safe_next(target):
    if not target:
        return False

    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))

    return (
        test.scheme in ("http", "https")
        and ref.netloc == test.netloc
    )


def log(action, detail=""):
    db.session.add(
        AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action,
            detail=detail
        )
    )


def send_email(to, subject, html):
    mode = current_app.config["MAIL_MODE"].lower()

    if mode == "console":
        print(
            f"\n--- EMAIL PREVIEW ---\n"
            f"TO: {to}\n"
            f"SUBJECT: {subject}\n"
            f"{html}\n"
            f"--- END EMAIL ---\n"
        )
        return True

    if mode != "smtp":
        raise RuntimeError("MAIL_MODE must be console or smtp")

    sender = current_app.config.get("MAIL_DEFAULT_SENDER")

    if (
        not sender
        or not current_app.config.get("MAIL_USERNAME")
        or not current_app.config.get("MAIL_PASSWORD")
    ):
        raise RuntimeError(
            "SMTP is selected but sender/username/password are not configured"
        )

    mail.send(
        Message(
            subject=subject,
            recipients=[to],
            html=html,
            sender=sender
        )
    )

    return True


def track_urls(campaign_id, subscriber_id, html):
    """Rewrite absolute HTTP(S) links to tracked first-party redirects."""
    pattern = re.compile(r'''href=["'](https?://[^"']+)["']''', re.I)

    def replace_link(match):
        return 'href="{}"'.format(
            url_for(
                "track_click",
                cid=campaign_id,
                sid=subscriber_id,
                target=match.group(1),
                _external=True
            )
        )

    return pattern.sub(replace_link, html)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    app.config.update(
        SECRET_KEY=os.getenv(
            "SECRET_KEY",
            "dev-only-change-this-secret"
        ),
        SQLALCHEMY_DATABASE_URI=os.getenv(
            "DATABASE_URL",
            "sqlite:///email_marketing.db"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAIL_MODE=os.getenv("MAIL_MODE", "console"),
        MAIL_SERVER=os.getenv("MAIL_SERVER", "localhost"),
        MAIL_PORT=int(os.getenv("MAIL_PORT", "587")),
        MAIL_USE_TLS=os.getenv("MAIL_USE_TLS", "true").lower() == "true",
        MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
        MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
        MAIL_DEFAULT_SENDER=os.getenv("MAIL_DEFAULT_SENDER")
    )

    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"
    mail.init_app(app)

    @app.context_processor
    def inject_counts():
        if current_user.is_authenticated:
            return {
                "nav_subscriber_count": Subscriber.query.count(),
                "nav_campaign_count": Campaign.query.filter_by(
                    created_by=current_user.id
                ).count()
            }

        return {}

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))

        return redirect(url_for("login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            if (
                len(username) < 3
                or len(password) < 8
                or not valid_email(email)
            ):
                flash(
                    "Enter a username (3+ chars), valid email, and password (8+ chars).",
                    "danger"
                )

            elif User.query.filter(
                (User.username == username) | (User.email == email)
            ).first():
                flash("Username or email already exists.", "danger")

            else:
                user = User(username=username, email=email)
                user.set_password(password)

                db.session.add(user)
                db.session.commit()

                login_user(user)
                flash("Account created.", "success")

                return redirect(url_for("dashboard"))

        return render_template("auth.html", mode="Register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            identity = request.form.get("identity", "").strip()

            user = User.query.filter(
                (User.username == identity)
                | (User.email == identity.lower())
            ).first()

            if user and user.check_password(
                request.form.get("password", "")
            ):
                login_user(user)

                target = request.args.get("next")

                if safe_next(target):
                    return redirect(target)

                return redirect(url_for("dashboard"))

            flash("Invalid login details.", "danger")

        return render_template("auth.html", mode="Login")

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been logged out.", "success")
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        campaigns = (
            Campaign.query
            .filter_by(created_by=current_user.id)
            .order_by(Campaign.created_at.desc())
            .limit(6)
            .all()
        )

        total = (
            CampaignRecipient.query
            .join(Campaign)
            .filter(Campaign.created_by == current_user.id)
            .count()
        )

        opens = (
            CampaignRecipient.query
            .join(Campaign)
            .filter(
                Campaign.created_by == current_user.id,
                CampaignRecipient.opened_at.isnot(None)
            )
            .count()
        )

        clicks = (
            CampaignRecipient.query
            .join(Campaign)
            .filter(
                Campaign.created_by == current_user.id,
                CampaignRecipient.clicked_at.isnot(None)
            )
            .count()
        )

        return render_template(
            "dashboard.html",
            campaigns=campaigns,
            subscribers=Subscriber.query.count(),
            active=Subscriber.query.filter_by(is_active=True).count(),
            total=total,
            opens=opens,
            clicks=clicks
        )

    @app.route("/subscribers", methods=["GET", "POST"])
    @login_required
    def subscribers():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()

            if not valid_email(email):
                flash("Enter a valid email address.", "danger")

            elif Subscriber.query.filter_by(email=email).first():
                flash("That subscriber already exists.", "warning")

            elif request.form.get("consent") != "yes":
                flash(
                    "Explicit consent is required before adding a subscriber.",
                    "danger"
                )

            else:
                subscriber = Subscriber(
                    email=email,
                    first_name=request.form.get("first_name", "").strip(),
                    last_name=request.form.get("last_name", "").strip(),
                    company=request.form.get("company", "").strip(),
                    source="manual"
                )

                db.session.add(subscriber)
                db.session.commit()

                log("subscriber.add", email)
                db.session.commit()

                flash("Subscriber added.", "success")

            return redirect(url_for("subscribers"))

        q = request.args.get("q", "").strip()
        query = Subscriber.query

        if q:
            query = query.filter(
                (Subscriber.email.contains(q))
                | (Subscriber.first_name.contains(q))
                | (Subscriber.last_name.contains(q))
            )

        return render_template(
            "subscribers.html",
            subscribers=query.order_by(
                Subscriber.created_at.desc()
            ).all(),
            q=q
        )

    @app.post("/subscribers/import")
    @login_required
    def import_subscribers():
        file = request.files.get("file")

        if not file or not file.filename.lower().endswith(".csv"):
            flash("Upload a CSV file.", "danger")
            return redirect(url_for("subscribers"))

        try:
            rows = csv.DictReader(
                io.StringIO(file.stream.read().decode("utf-8-sig"))
            )

            added = 0

            for row in rows:
                email = (row.get("email") or "").strip().lower()

                consent = (
                    (row.get("consent") or "").strip().lower()
                    in ("yes", "true", "1")
                )

                if (
                    valid_email(email)
                    and consent
                    and not Subscriber.query.filter_by(email=email).first()
                ):
                    db.session.add(
                        Subscriber(
                            email=email,
                            first_name=row.get("first_name", "")[:80],
                            last_name=row.get("last_name", "")[:80],
                            company=row.get("company", "")[:120],
                            source="csv"
                        )
                    )
                    added += 1

            db.session.commit()

            log("subscriber.import", f"{added} imported")
            db.session.commit()

            flash(
                f"Imported {added} consented subscriber(s).",
                "success"
            )

        except Exception as exc:
            db.session.rollback()
            flash(f"CSV import failed: {exc}", "danger")

        return redirect(url_for("subscribers"))

    @app.post("/subscribers/<int:sid>/toggle")
    @login_required
    def toggle_subscriber(sid):
        subscriber = db.get_or_404(Subscriber, sid)

        subscriber.is_active = not subscriber.is_active

        subscriber.unsubscribed_at = (
            None if subscriber.is_active else now()
        )

        db.session.commit()

        flash("Subscriber status updated.", "success")
        return redirect(url_for("subscribers"))

    @app.route("/segments", methods=["GET", "POST"])
    @login_required
    def segments():
        if request.method == "POST":
            name = request.form.get("name", "").strip()

            if name:
                segment = Segment(
                    name=name,
                    description=request.form.get("description", "")
                )

                db.session.add(segment)
                db.session.commit()

                ids = request.form.getlist("subscriber_ids")

                for sid in ids:
                    if db.session.get(Subscriber, int(sid)):
                        db.session.add(
                            SegmentMember(
                                segment_id=segment.id,
                                subscriber_id=int(sid)
                            )
                        )

                db.session.commit()
                flash("Segment created.", "success")

            return redirect(url_for("segments"))

        return render_template(
            "segments.html",
            segments=Segment.query.order_by(Segment.id.desc()).all(),
            subscribers=Subscriber.query.filter_by(is_active=True).all()
        )

    @app.route("/templates", methods=["GET", "POST"])
    @login_required
    def templates():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            subject = request.form.get("subject", "").strip()
            content = request.form.get("html_content", "").strip()

            if name and subject and content:
                db.session.add(
                    EmailTemplate(
                        name=name,
                        subject=subject,
                        html_content=content,
                        created_by=current_user.id
                    )
                )

                db.session.commit()
                flash("Template saved.", "success")

            else:
                flash("All template fields are required.", "danger")

            return redirect(url_for("templates"))

        return render_template(
            "templates.html",
            templates=EmailTemplate.query.filter_by(
                created_by=current_user.id
            ).order_by(EmailTemplate.id.desc()).all()
        )

    @app.route("/campaigns", methods=["GET", "POST"])
    @login_required
    def campaigns():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            subject = request.form.get("subject", "").strip()
            content = request.form.get("html_content", "").strip()

            if not all((name, subject, content)):
                flash(
                    "Campaign name, subject and content are required.",
                    "danger"
                )

            else:
                segment_id = request.form.get("segment_id") or None

                campaign = Campaign(
                    name=name,
                    subject=subject,
                    html_content=content,
                    created_by=current_user.id,
                    segment_id=int(segment_id) if segment_id else None
                )

                db.session.add(campaign)
                db.session.commit()

                log("campaign.create", campaign.name)
                db.session.commit()

                flash("Campaign draft created.", "success")

            return redirect(url_for("campaigns"))

        return render_template(
            "campaigns.html",
            campaigns=Campaign.query.filter_by(
                created_by=current_user.id
            ).order_by(Campaign.created_at.desc()).all(),
            segments=Segment.query.all()
        )

    @app.post("/campaigns/<int:cid>/send")
    @login_required
    def send_campaign(cid):
        campaign = Campaign.query.filter_by(
            id=cid,
            created_by=current_user.id
        ).first_or_404()

        if campaign.status not in ("draft", "paused"):
            flash("Only draft or paused campaigns can be sent.", "danger")
            return redirect(url_for("campaigns"))

        recipients = Subscriber.query.filter_by(is_active=True)

        if campaign.segment_id:
            recipients = (
                recipients
                .join(
                    SegmentMember,
                    SegmentMember.subscriber_id == Subscriber.id
                )
                .filter(SegmentMember.segment_id == campaign.segment_id)
            )

        sent = 0

        try:
            for subscriber in recipients.all():
                existing = CampaignRecipient.query.filter_by(
                    campaign_id=campaign.id,
                    subscriber_id=subscriber.id
                ).first()

                if existing:
                    continue

                body = track_urls(
                    campaign.id,
                    subscriber.id,
                    campaign.html_content
                )

                token = URLSafeTimedSerializer(
                    current_app.config["SECRET_KEY"]
                ).dumps(subscriber.id, salt="unsubscribe")

                pixel = url_for(
                    "track_open",
                    cid=campaign.id,
                    sid=subscriber.id,
                    _external=True
                )

                body += (
                    f'<img src="{pixel}" width="1" height="1" '
                    'alt="" style="display:none">'
                )

                unsubscribe_url = url_for(
                    "unsubscribe",
                    token=token,
                    _external=True
                )

                body += (
                    '<p style="font-size:10px">'
                    f'<a href="{unsubscribe_url}">Unsubscribe</a>'
                    '</p>'
                )

                send_email(
                    subscriber.email,
                    campaign.subject,
                    body
                )

                db.session.add(
                    CampaignRecipient(
                        campaign_id=campaign.id,
                        subscriber_id=subscriber.id
                    )
                )

                sent += 1

            campaign.status = "sent"
            campaign.sent_at = now()

            db.session.commit()

            log("campaign.send", f"{campaign.name}: {sent}")
            db.session.commit()

            flash(
                f"Campaign processed for {sent} subscriber(s).",
                "success"
            )

        except Exception as exc:
            db.session.rollback()
            flash(f"Sending stopped: {exc}", "danger")

        return redirect(url_for("campaigns"))

    @app.post("/campaigns/<int:cid>/schedule")
    @login_required
    def schedule_campaign(cid):
        campaign = Campaign.query.filter_by(
            id=cid,
            created_by=current_user.id
        ).first_or_404()

        try:
            scheduled_at = datetime.fromisoformat(
                request.form.get("scheduled_at", "")
            )

            if scheduled_at <= now():
                raise ValueError()

            campaign.scheduled_at = scheduled_at
            campaign.status = "scheduled"

            db.session.commit()

            flash(
                "Campaign scheduled. Run the scheduler command at the scheduled time.",
                "success"
            )

        except ValueError:
            flash("Choose a valid future date and time.", "danger")

        return redirect(url_for("campaigns"))

    @app.get("/track/open/<int:cid>/<int:sid>.gif")
    def track_open(cid, sid):
        recipient = CampaignRecipient.query.filter_by(
            campaign_id=cid,
            subscriber_id=sid
        ).first()

        if recipient and not recipient.opened_at:
            recipient.opened_at = now()
            db.session.commit()

        gif = bytes.fromhex(
            "47494638396101000100800000000000ffffff"
            "21f90401000000002c00000000010001000002024401003b"
        )

        return Response(
            gif,
            mimetype="image/gif",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )

    @app.get("/track/click/<int:cid>/<int:sid>")
    def track_click(cid, sid):
        target = request.args.get("target", "")
        parsed = urlparse(target)

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            abort(400)

        recipient = CampaignRecipient.query.filter_by(
            campaign_id=cid,
            subscriber_id=sid
        ).first()

        if recipient:
            recipient.clicked_at = now()
            db.session.commit()

        return redirect(target)

    @app.get("/unsubscribe/<token>")
    def unsubscribe(token):
        serializer = URLSafeTimedSerializer(
            current_app.config["SECRET_KEY"]
        )

        try:
            subscriber_id = serializer.loads(
                token,
                salt="unsubscribe",
                max_age=60 * 60 * 24 * 365
            )

        except (BadSignature, SignatureExpired):
            return "Invalid or expired unsubscribe link", 400

        subscriber = db.session.get(Subscriber, subscriber_id)

        if not subscriber:
            return "Subscriber not found", 404

        subscriber.is_active = False
        subscriber.unsubscribed_at = now()

        db.session.commit()

        return "You have been unsubscribed successfully.", 200

    @app.route("/sequences", methods=["GET", "POST"])
    @login_required
    def sequences():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            subject = request.form.get("subject", "").strip()
            content = request.form.get("html_content", "").strip()

            try:
                delay = max(
                    0,
                    int(request.form.get("delay_days", "0"))
                )
            except ValueError:
                delay = 0

            if name and subject and content:
                sequence = AutomationSequence(
                    name=name,
                    subject=subject,
                    html_content=content,
                    delay_days=delay,
                    created_by=current_user.id
                )

                db.session.add(sequence)
                db.session.commit()

                flash("Automation sequence created.", "success")

            return redirect(url_for("sequences"))

        return render_template(
            "sequences.html",
            sequences=AutomationSequence.query.filter_by(
                created_by=current_user.id
            ).all(),
            subscribers=Subscriber.query.filter_by(is_active=True).all()
        )

    @app.post("/sequences/<int:seqid>/enroll")
    @login_required
    def enroll_sequence(seqid):
        sequence = AutomationSequence.query.filter_by(
            id=seqid,
            created_by=current_user.id
        ).first_or_404()

        added = 0

        for sid in request.form.getlist("subscriber_ids"):
            subscriber = db.session.get(Subscriber, int(sid))

            if (
                subscriber
                and subscriber.is_active
                and not SequenceEnrollment.query.filter_by(
                    sequence_id=sequence.id,
                    subscriber_id=subscriber.id
                ).first()
            ):
                db.session.add(
                    SequenceEnrollment(
                        sequence_id=sequence.id,
                        subscriber_id=subscriber.id,
                        due_at=now() + timedelta(days=sequence.delay_days)
                    )
                )

                added += 1

        db.session.commit()

        flash(f"Enrolled {added} subscriber(s).", "success")
        return redirect(url_for("sequences"))

    @app.cli.command("init-db")
    def init_db():
        """Create database tables."""
        db.create_all()
        print("Database tables created.")

    @app.cli.command("run-scheduler")
    def run_scheduler():
        due = SequenceEnrollment.query.filter(
            SequenceEnrollment.sent_at.is_(None),
            SequenceEnrollment.due_at <= now()
        ).all()

        count = 0

        for enrollment in due:
            sequence = db.session.get(
                AutomationSequence,
                enrollment.sequence_id
            )

            subscriber = db.session.get(
                Subscriber,
                enrollment.subscriber_id
            )

            if (
                sequence
                and sequence.active
                and subscriber
                and subscriber.is_active
            ):
                send_email(
                    subscriber.email,
                    sequence.subject,
                    sequence.html_content
                )

                enrollment.sent_at = now()
                count += 1

        scheduled_campaigns = Campaign.query.filter_by(
            status="scheduled"
        ).filter(
            Campaign.scheduled_at <= now()
        ).all()

        for campaign in scheduled_campaigns:
            recipients = Subscriber.query.filter_by(is_active=True)

            if campaign.segment_id:
                recipients = (
                    recipients
                    .join(
                        SegmentMember,
                        SegmentMember.subscriber_id == Subscriber.id
                    )
                    .filter(
                        SegmentMember.segment_id == campaign.segment_id
                    )
                )

            for subscriber in recipients.all():
                existing = CampaignRecipient.query.filter_by(
                    campaign_id=campaign.id,
                    subscriber_id=subscriber.id
                ).first()

                if not existing:
                    send_email(
                        subscriber.email,
                        campaign.subject,
                        campaign.html_content
                    )

                    db.session.add(
                        CampaignRecipient(
                            campaign_id=campaign.id,
                            subscriber_id=subscriber.id
                        )
                    )

            campaign.status = "sent"
            campaign.sent_at = now()
            count += 1

        db.session.commit()
        print(f"Scheduler processed {count} item(s).")

    @app.route("/analytics")
    @login_required
    def analytics():
        campaigns = Campaign.query.filter_by(
            created_by=current_user.id
        ).all()

        rows = []

        for campaign in campaigns:
            recipients = len(campaign.recipients)

            opens = sum(
                1 for recipient in campaign.recipients
                if recipient.opened_at
            )

            clicks = sum(
                1 for recipient in campaign.recipients
                if recipient.clicked_at
            )

            rows.append({
                "campaign": campaign,
                "recipients": recipients,
                "opens": opens,
                "clicks": clicks,
                "open_rate": round(
                    opens / recipients * 100, 1
                ) if recipients else 0,
                "click_rate": round(
                    clicks / recipients * 100, 1
                ) if recipients else 0
            })

        return render_template("analytics.html", rows=rows)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.errorhandler(413)
    def too_large(_):
        return "File too large", 413

    return app


app = create_app()