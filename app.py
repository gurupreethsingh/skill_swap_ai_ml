from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from collections import Counter
import os
import secrets
import re
import math


app = Flask(__name__)
app.secret_key = "skill_swap_secret_key"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(INSTANCE_DIR, "skillswap.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ============================================================
# DATABASE MODELS
# ============================================================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(50), default="user")
    skills_offered = db.Column(db.String(255), default="")
    skills_wanted = db.Column(db.String(255), default="")

    reset_token = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SkillExchangeRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    requested_skill = db.Column(db.String(150), nullable=False)
    offered_skill = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(50), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    responded_at = db.Column(db.DateTime, nullable=True)

    sender = db.relationship("User", foreign_keys=[sender_id], backref="sent_requests")
    receiver = db.relationship("User", foreign_keys=[receiver_id], backref="received_requests")


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    request_id = db.Column(db.Integer, db.ForeignKey("skill_exchange_request.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    exchange_request = db.relationship("SkillExchangeRequest", backref="chat_messages")
    sender = db.relationship("User", foreign_keys=[sender_id], backref="sent_chat_messages")
    receiver = db.relationship("User", foreign_keys=[receiver_id], backref="received_chat_messages")


class SkillReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    request_id = db.Column(db.Integer, db.ForeignKey("skill_exchange_request.id"), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reviewed_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    rating = db.Column(db.Integer, nullable=False)
    review_text = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    exchange_request = db.relationship("SkillExchangeRequest", backref="reviews")
    reviewer = db.relationship("User", foreign_keys=[reviewer_id], backref="given_reviews")
    reviewed_user = db.relationship("User", foreign_keys=[reviewed_user_id], backref="received_reviews")


# ============================================================
# BASIC HELPERS
# ============================================================

def login_required():
    return "user_id" in session


def superadmin_required():
    return session.get("role") == "superadmin"


def get_logged_user():
    if "user_id" in session:
        return User.query.get(session["user_id"])
    return None


def split_skills(text):
    if not text:
        return []
    return [skill.strip() for skill in text.split(",") if skill.strip()]


def get_other_user_for_request(exchange_request, current_user_id):
    if exchange_request.sender_id == current_user_id:
        return exchange_request.receiver
    return exchange_request.sender


def user_can_access_request(exchange_request, user_id):
    return exchange_request.sender_id == user_id or exchange_request.receiver_id == user_id


# ============================================================
# MODULE 2: AI MATCHMAKING HELPERS
# ============================================================

def clean_skill_text(text):
    if not text:
        return []

    text = text.lower()
    text = re.sub(r"[^a-z0-9,+#.\s]", " ", text)

    cleaned_skills = []

    for part in text.split(","):
        skill = part.strip()
        if skill:
            cleaned_skills.append(skill)

    return cleaned_skills


def tokenize_skills(skills):
    tokens = []

    for skill in skills:
        skill = skill.strip().lower()

        if skill:
            tokens.append(skill)

        for word in skill.split():
            word = word.strip()
            if len(word) > 1:
                tokens.append(word)

    return tokens


def cosine_similarity(list_a, list_b):
    if not list_a or not list_b:
        return 0

    counter_a = Counter(list_a)
    counter_b = Counter(list_b)

    all_tokens = set(counter_a.keys()).union(set(counter_b.keys()))
    dot_product = 0

    for token in all_tokens:
        dot_product += counter_a[token] * counter_b[token]

    magnitude_a = math.sqrt(sum(value * value for value in counter_a.values()))
    magnitude_b = math.sqrt(sum(value * value for value in counter_b.values()))

    if magnitude_a == 0 or magnitude_b == 0:
        return 0

    return dot_product / (magnitude_a * magnitude_b)


def calculate_match_score(current_user, other_user):
    current_wanted = clean_skill_text(current_user.skills_wanted)
    current_offered = clean_skill_text(current_user.skills_offered)

    other_offered = clean_skill_text(other_user.skills_offered)
    other_wanted = clean_skill_text(other_user.skills_wanted)

    learning_score = cosine_similarity(
        tokenize_skills(current_wanted),
        tokenize_skills(other_offered)
    )

    teaching_score = cosine_similarity(
        tokenize_skills(current_offered),
        tokenize_skills(other_wanted)
    )

    final_score = ((learning_score * 0.6) + (teaching_score * 0.4)) * 100

    direct_learning_matches = set(current_wanted).intersection(set(other_offered))
    direct_teaching_matches = set(current_offered).intersection(set(other_wanted))

    reason_parts = []

    if direct_learning_matches:
        reason_parts.append("They can teach you: " + ", ".join(sorted(direct_learning_matches)))

    if direct_teaching_matches:
        reason_parts.append("You can teach them: " + ", ".join(sorted(direct_teaching_matches)))

    if not reason_parts:
        if final_score >= 40:
            reason_parts.append("Similar skill interest detected.")
        elif final_score > 0:
            reason_parts.append("Some related learning interest was detected.")
        else:
            reason_parts.append("No strong skill exchange found.")

    return {
        "score": round(final_score, 2),
        "learning_score": round(learning_score * 100, 2),
        "teaching_score": round(teaching_score * 100, 2),
        "reason": " | ".join(reason_parts),
    }


def get_ai_matches_for_user(current_user, limit=10):
    other_users = User.query.filter(User.id != current_user.id).all()
    matches = []

    for other_user in other_users:
        result = calculate_match_score(current_user, other_user)

        if result["score"] > 0:
            matches.append({
                "user": other_user,
                "score": result["score"],
                "learning_score": result["learning_score"],
                "teaching_score": result["teaching_score"],
                "reason": result["reason"],
            })

    matches = sorted(matches, key=lambda item: item["score"], reverse=True)
    return matches[:limit]


def get_platform_matchmaking_stats():
    users = User.query.all()

    total_pairs_checked = 0
    active_match_count = 0
    best_score = 0
    total_score = 0

    for current_user in users:
        for other_user in users:
            if current_user.id == other_user.id:
                continue

            total_pairs_checked += 1
            score = calculate_match_score(current_user, other_user)["score"]

            if score > 0:
                active_match_count += 1
                total_score += score
                best_score = max(best_score, score)

    average_score = round(total_score / active_match_count, 2) if active_match_count else 0

    return {
        "total_pairs_checked": total_pairs_checked,
        "active_match_count": active_match_count,
        "best_score": round(best_score, 2),
        "average_score": average_score,
    }


# ============================================================
# MODULE 3: REQUEST HELPERS
# ============================================================

def get_request_stats_for_user(user_id):
    sent_total = SkillExchangeRequest.query.filter_by(sender_id=user_id).count()
    received_total = SkillExchangeRequest.query.filter_by(receiver_id=user_id).count()

    pending_received = SkillExchangeRequest.query.filter_by(
        receiver_id=user_id,
        status="pending"
    ).count()

    accepted_total = SkillExchangeRequest.query.filter(
        (
            (SkillExchangeRequest.sender_id == user_id)
            | (SkillExchangeRequest.receiver_id == user_id)
        ),
        SkillExchangeRequest.status == "accepted"
    ).count()

    rejected_total = SkillExchangeRequest.query.filter(
        (
            (SkillExchangeRequest.sender_id == user_id)
            | (SkillExchangeRequest.receiver_id == user_id)
        ),
        SkillExchangeRequest.status == "rejected"
    ).count()

    return {
        "sent_total": sent_total,
        "received_total": received_total,
        "pending_received": pending_received,
        "accepted_total": accepted_total,
        "rejected_total": rejected_total,
    }


def get_platform_request_stats():
    return {
        "total_requests": SkillExchangeRequest.query.count(),
        "pending_requests": SkillExchangeRequest.query.filter_by(status="pending").count(),
        "accepted_requests": SkillExchangeRequest.query.filter_by(status="accepted").count(),
        "rejected_requests": SkillExchangeRequest.query.filter_by(status="rejected").count(),
        "cancelled_requests": SkillExchangeRequest.query.filter_by(status="cancelled").count(),
    }


# ============================================================
# MODULE 4: CHAT HELPERS
# ============================================================

def get_chat_stats_for_user(user_id):
    total_messages = ChatMessage.query.filter(
        (
            (ChatMessage.sender_id == user_id)
            | (ChatMessage.receiver_id == user_id)
        )
    ).count()

    unread_messages = ChatMessage.query.filter_by(
        receiver_id=user_id,
        is_read=False
    ).count()

    active_chats = db.session.query(ChatMessage.request_id).filter(
        (
            (ChatMessage.sender_id == user_id)
            | (ChatMessage.receiver_id == user_id)
        )
    ).distinct().count()

    accepted_requests = SkillExchangeRequest.query.filter(
        (
            (SkillExchangeRequest.sender_id == user_id)
            | (SkillExchangeRequest.receiver_id == user_id)
        ),
        SkillExchangeRequest.status == "accepted"
    ).count()

    return {
        "total_messages": total_messages,
        "unread_messages": unread_messages,
        "active_chats": active_chats,
        "accepted_requests": accepted_requests,
    }


def get_platform_chat_stats():
    return {
        "total_messages": ChatMessage.query.count(),
        "unread_messages": ChatMessage.query.filter_by(is_read=False).count(),
        "active_chats": db.session.query(ChatMessage.request_id).distinct().count(),
        "active_senders": db.session.query(ChatMessage.sender_id).distinct().count(),
    }


def get_recent_chats_for_user(user_id, limit=4):
    accepted_requests = SkillExchangeRequest.query.filter(
        (
            (SkillExchangeRequest.sender_id == user_id)
            | (SkillExchangeRequest.receiver_id == user_id)
        ),
        SkillExchangeRequest.status == "accepted"
    ).order_by(SkillExchangeRequest.responded_at.desc()).limit(limit).all()

    chat_rows = []

    for exchange_request in accepted_requests:
        other_user = get_other_user_for_request(exchange_request, user_id)

        last_message = ChatMessage.query.filter_by(
            request_id=exchange_request.id
        ).order_by(ChatMessage.created_at.desc()).first()

        unread_count = ChatMessage.query.filter_by(
            request_id=exchange_request.id,
            receiver_id=user_id,
            is_read=False
        ).count()

        chat_rows.append({
            "request": exchange_request,
            "other_user": other_user,
            "last_message": last_message,
            "unread_count": unread_count,
        })

    return chat_rows


# ============================================================
# MODULE 5: REVIEW HELPERS
# ============================================================

def get_review_stats_for_user(user_id):
    received_reviews = SkillReview.query.filter_by(reviewed_user_id=user_id).all()
    given_total = SkillReview.query.filter_by(reviewer_id=user_id).count()

    received_total = len(received_reviews)

    if received_total:
        average_rating = round(
            sum(review.rating for review in received_reviews) / received_total,
            2
        )
    else:
        average_rating = 0

    five_star_count = SkillReview.query.filter_by(
        reviewed_user_id=user_id,
        rating=5
    ).count()

    return {
        "given_total": given_total,
        "received_total": received_total,
        "average_rating": average_rating,
        "five_star_count": five_star_count,
    }


def get_platform_review_stats():
    total_reviews = SkillReview.query.count()

    if total_reviews:
        all_reviews = SkillReview.query.all()
        average_rating = round(
            sum(review.rating for review in all_reviews) / total_reviews,
            2
        )
    else:
        average_rating = 0

    five_star_reviews = SkillReview.query.filter_by(rating=5).count()

    return {
        "total_reviews": total_reviews,
        "average_rating": average_rating,
        "five_star_reviews": five_star_reviews,
        "low_reviews": SkillReview.query.filter(SkillReview.rating <= 2).count(),
    }


def has_user_reviewed_request(request_id, reviewer_id):
    existing_review = SkillReview.query.filter_by(
        request_id=request_id,
        reviewer_id=reviewer_id
    ).first()

    return existing_review is not None


# ============================================================
# AUTH ROUTES
# ============================================================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        skills_offered = request.form.get("skills_offered", "").strip()
        skills_wanted = request.form.get("skills_wanted", "").strip()

        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            flash("Email already registered. Please login.", "danger")
            return redirect(url_for("login"))

        new_user = User(
            full_name=full_name,
            email=email,
            password=generate_password_hash(password),
            skills_offered=skills_offered,
            skills_wanted=skills_wanted,
            role="user"
        )

        db.session.add(new_user)
        db.session.commit()

        flash("Account created successfully. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password, password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        session["user_id"] = user.id
        session["full_name"] = user.full_name
        session["role"] = user.role

        flash("Login successful.", "success")

        if user.role == "superadmin":
            return redirect(url_for("superadmin_dashboard"))

        return redirect(url_for("user_dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    reset_link = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if not user:
            flash("No account found with this email.", "danger")
            return redirect(url_for("forgot_password"))

        token = secrets.token_urlsafe(32)
        user.reset_token = token
        db.session.commit()

        reset_link = url_for("reset_password", token=token, _external=True)
        flash("Reset link generated successfully.", "success")

    return render_template("forgot_password.html", reset_link=reset_link)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()

    if not user:
        flash("Invalid or expired reset link.", "danger")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "").strip()
        user.password = generate_password_hash(new_password)
        user.reset_token = None
        db.session.commit()

        flash("Password reset successful. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


# ============================================================
# DASHBOARD ROUTES
# ============================================================

@app.route("/user-dashboard")
def user_dashboard():
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    user = get_logged_user()

    request_stats = get_request_stats_for_user(user.id)
    chat_stats = get_chat_stats_for_user(user.id)
    review_stats = get_review_stats_for_user(user.id)
    suggested_matches = get_ai_matches_for_user(user, limit=3)

    recent_received_requests = SkillExchangeRequest.query.filter_by(
        receiver_id=user.id
    ).order_by(SkillExchangeRequest.created_at.desc()).limit(4).all()

    recent_sent_requests = SkillExchangeRequest.query.filter_by(
        sender_id=user.id
    ).order_by(SkillExchangeRequest.created_at.desc()).limit(4).all()

    recent_chats = get_recent_chats_for_user(user.id, limit=4)

    recent_reviews = SkillReview.query.filter_by(
        reviewed_user_id=user.id
    ).order_by(SkillReview.created_at.desc()).limit(4).all()

    stats = {
        "skills_offered": len(split_skills(user.skills_offered)),
        "skills_wanted": len(split_skills(user.skills_wanted)),
        "matches": len(get_ai_matches_for_user(user, limit=100)),
        "reviews": review_stats["received_total"],
        "pending_requests": request_stats["pending_received"],
    }

    return render_template(
        "user_dashboard.html",
        user=user,
        stats=stats,
        suggested_matches=suggested_matches,
        request_stats=request_stats,
        chat_stats=chat_stats,
        review_stats=review_stats,
        recent_received_requests=recent_received_requests,
        recent_sent_requests=recent_sent_requests,
        recent_chats=recent_chats,
        recent_reviews=recent_reviews
    )


@app.route("/superadmin-dashboard")
def superadmin_dashboard():
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    if not superadmin_required():
        flash("Access denied. Superadmin only.", "danger")
        return redirect(url_for("user_dashboard"))

    users = User.query.order_by(User.created_at.desc()).all()

    total_users = User.query.count()
    total_superadmins = User.query.filter_by(role="superadmin").count()
    total_normal_users = User.query.filter_by(role="user").count()

    matchmaking_stats = get_platform_matchmaking_stats()
    request_stats = get_platform_request_stats()
    chat_stats = get_platform_chat_stats()
    review_stats = get_platform_review_stats()

    recent_requests = SkillExchangeRequest.query.order_by(
        SkillExchangeRequest.created_at.desc()
    ).limit(8).all()

    recent_messages = ChatMessage.query.order_by(
        ChatMessage.created_at.desc()
    ).limit(8).all()

    recent_reviews = SkillReview.query.order_by(
        SkillReview.created_at.desc()
    ).limit(8).all()

    return render_template(
        "superadmin_dashboard.html",
        users=users,
        total_users=total_users,
        total_superadmins=total_superadmins,
        total_normal_users=total_normal_users,
        matchmaking_stats=matchmaking_stats,
        request_stats=request_stats,
        chat_stats=chat_stats,
        review_stats=review_stats,
        recent_requests=recent_requests,
        recent_messages=recent_messages,
        recent_reviews=recent_reviews
    )


@app.route("/update-role/<int:user_id>", methods=["POST"])
def update_role(user_id):
    if not login_required() or not superadmin_required():
        flash("Unauthorized action.", "danger")
        return redirect(url_for("login"))

    user = User.query.get_or_404(user_id)
    new_role = request.form.get("role")

    if new_role not in ["user", "superadmin"]:
        flash("Invalid role selected.", "danger")
        return redirect(url_for("superadmin_dashboard"))

    user.role = new_role
    db.session.commit()

    flash("User role updated successfully.", "success")
    return redirect(url_for("superadmin_dashboard"))


# ============================================================
# MODULE 2 ROUTES
# ============================================================

@app.route("/matches")
def matches():
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    current_user = get_logged_user()

    if not current_user:
        flash("User not found. Please login again.", "danger")
        return redirect(url_for("login"))

    suggested_matches = get_ai_matches_for_user(current_user, limit=15)

    return render_template(
        "matches.html",
        user=current_user,
        suggested_matches=suggested_matches
    )


# ============================================================
# MODULE 3 ROUTES
# ============================================================

@app.route("/send-request/<int:receiver_id>", methods=["GET", "POST"])
def send_request(receiver_id):
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    sender = get_logged_user()
    receiver = User.query.get_or_404(receiver_id)

    if sender.id == receiver.id:
        flash("You cannot send request to yourself.", "danger")
        return redirect(url_for("matches"))

    existing_pending = SkillExchangeRequest.query.filter_by(
        sender_id=sender.id,
        receiver_id=receiver.id,
        status="pending"
    ).first()

    if existing_pending:
        flash("You already have a pending request with this user.", "warning")
        return redirect(url_for("my_requests"))

    if request.method == "POST":
        requested_skill = request.form.get("requested_skill", "").strip()
        offered_skill = request.form.get("offered_skill", "").strip()
        message = request.form.get("message", "").strip()

        if not requested_skill or not offered_skill:
            flash("Please select both requested skill and offered skill.", "danger")
            return redirect(url_for("send_request", receiver_id=receiver.id))

        exchange_request = SkillExchangeRequest(
            sender_id=sender.id,
            receiver_id=receiver.id,
            requested_skill=requested_skill,
            offered_skill=offered_skill,
            message=message,
            status="pending"
        )

        db.session.add(exchange_request)
        db.session.commit()

        flash("Skill exchange request sent successfully.", "success")
        return redirect(url_for("my_requests"))

    return render_template(
        "send_request.html",
        sender=sender,
        receiver=receiver,
        sender_offered_skills=split_skills(sender.skills_offered),
        sender_wanted_skills=split_skills(sender.skills_wanted),
        receiver_offered_skills=split_skills(receiver.skills_offered),
        receiver_wanted_skills=split_skills(receiver.skills_wanted)
    )


@app.route("/my-requests")
def my_requests():
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    user = get_logged_user()

    received_requests = SkillExchangeRequest.query.filter_by(
        receiver_id=user.id
    ).order_by(SkillExchangeRequest.created_at.desc()).all()

    sent_requests = SkillExchangeRequest.query.filter_by(
        sender_id=user.id
    ).order_by(SkillExchangeRequest.created_at.desc()).all()

    request_stats = get_request_stats_for_user(user.id)

    return render_template(
        "my_requests.html",
        user=user,
        received_requests=received_requests,
        sent_requests=sent_requests,
        request_stats=request_stats,
        has_user_reviewed_request=has_user_reviewed_request
    )


@app.route("/request/<int:request_id>/accept", methods=["POST"])
def accept_request(request_id):
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    user = get_logged_user()
    exchange_request = SkillExchangeRequest.query.get_or_404(request_id)

    if exchange_request.receiver_id != user.id:
        flash("Only the receiver can accept this request.", "danger")
        return redirect(url_for("my_requests"))

    if exchange_request.status != "pending":
        flash("This request is already processed.", "warning")
        return redirect(url_for("my_requests"))

    exchange_request.status = "accepted"
    exchange_request.responded_at = datetime.utcnow()

    db.session.commit()

    flash("Request accepted successfully. Chat is now enabled.", "success")
    return redirect(url_for("chat_room", request_id=exchange_request.id))


@app.route("/request/<int:request_id>/reject", methods=["POST"])
def reject_request(request_id):
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    user = get_logged_user()
    exchange_request = SkillExchangeRequest.query.get_or_404(request_id)

    if exchange_request.receiver_id != user.id:
        flash("Only the receiver can reject this request.", "danger")
        return redirect(url_for("my_requests"))

    if exchange_request.status != "pending":
        flash("This request is already processed.", "warning")
        return redirect(url_for("my_requests"))

    exchange_request.status = "rejected"
    exchange_request.responded_at = datetime.utcnow()

    db.session.commit()

    flash("Request rejected.", "success")
    return redirect(url_for("my_requests"))


@app.route("/request/<int:request_id>/cancel", methods=["POST"])
def cancel_request(request_id):
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    user = get_logged_user()
    exchange_request = SkillExchangeRequest.query.get_or_404(request_id)

    if exchange_request.sender_id != user.id:
        flash("Only the sender can cancel this request.", "danger")
        return redirect(url_for("my_requests"))

    if exchange_request.status != "pending":
        flash("Only pending requests can be cancelled.", "warning")
        return redirect(url_for("my_requests"))

    exchange_request.status = "cancelled"
    exchange_request.responded_at = datetime.utcnow()

    db.session.commit()

    flash("Request cancelled.", "success")
    return redirect(url_for("my_requests"))


@app.route("/admin/requests")
def admin_requests():
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    if not superadmin_required():
        flash("Access denied. Superadmin only.", "danger")
        return redirect(url_for("user_dashboard"))

    status_filter = request.args.get("status", "all")
    query = SkillExchangeRequest.query

    if status_filter in ["pending", "accepted", "rejected", "cancelled"]:
        query = query.filter_by(status=status_filter)

    all_requests = query.order_by(SkillExchangeRequest.created_at.desc()).all()
    request_stats = get_platform_request_stats()

    return render_template(
        "admin_requests.html",
        all_requests=all_requests,
        request_stats=request_stats,
        status_filter=status_filter
    )


# ============================================================
# MODULE 4 ROUTES
# ============================================================

@app.route("/my-chats")
def my_chats():
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    user = get_logged_user()

    accepted_requests = SkillExchangeRequest.query.filter(
        (
            (SkillExchangeRequest.sender_id == user.id)
            | (SkillExchangeRequest.receiver_id == user.id)
        ),
        SkillExchangeRequest.status == "accepted"
    ).order_by(SkillExchangeRequest.responded_at.desc()).all()

    chat_rows = []

    for exchange_request in accepted_requests:
        other_user = get_other_user_for_request(exchange_request, user.id)

        last_message = ChatMessage.query.filter_by(
            request_id=exchange_request.id
        ).order_by(ChatMessage.created_at.desc()).first()

        unread_count = ChatMessage.query.filter_by(
            request_id=exchange_request.id,
            receiver_id=user.id,
            is_read=False
        ).count()

        chat_rows.append({
            "request": exchange_request,
            "other_user": other_user,
            "last_message": last_message,
            "unread_count": unread_count
        })

    chat_stats = get_chat_stats_for_user(user.id)

    return render_template(
        "my_chats.html",
        user=user,
        chat_rows=chat_rows,
        chat_stats=chat_stats
    )


@app.route("/chat/<int:request_id>", methods=["GET", "POST"])
def chat_room(request_id):
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    user = get_logged_user()
    exchange_request = SkillExchangeRequest.query.get_or_404(request_id)

    if not user_can_access_request(exchange_request, user.id):
        flash("You do not have access to this chat.", "danger")
        return redirect(url_for("my_chats"))

    if exchange_request.status != "accepted":
        flash("Chat opens only after request is accepted.", "warning")
        return redirect(url_for("my_requests"))

    other_user = get_other_user_for_request(exchange_request, user.id)

    ChatMessage.query.filter_by(
        request_id=exchange_request.id,
        receiver_id=user.id,
        is_read=False
    ).update({"is_read": True})

    db.session.commit()

    if request.method == "POST":
        message_text = request.form.get("message", "").strip()

        if not message_text:
            flash("Message cannot be empty.", "danger")
            return redirect(url_for("chat_room", request_id=exchange_request.id))

        chat_message = ChatMessage(
            request_id=exchange_request.id,
            sender_id=user.id,
            receiver_id=other_user.id,
            message=message_text,
            is_read=False
        )

        db.session.add(chat_message)
        db.session.commit()

        return redirect(url_for("chat_room", request_id=exchange_request.id))

    messages = ChatMessage.query.filter_by(
        request_id=exchange_request.id
    ).order_by(ChatMessage.created_at.asc()).all()

    return render_template(
        "chat_room.html",
        user=user,
        other_user=other_user,
        exchange_request=exchange_request,
        messages=messages,
        has_user_reviewed_request=has_user_reviewed_request
    )


@app.route("/admin/chats")
def admin_chats():
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    if not superadmin_required():
        flash("Access denied. Superadmin only.", "danger")
        return redirect(url_for("user_dashboard"))

    chat_stats = get_platform_chat_stats()

    all_messages = ChatMessage.query.order_by(
        ChatMessage.created_at.desc()
    ).limit(100).all()

    return render_template(
        "admin_chats.html",
        chat_stats=chat_stats,
        all_messages=all_messages
    )


# ============================================================
# MODULE 5 ROUTES
# ============================================================

@app.route("/review/<int:request_id>", methods=["GET", "POST"])
def add_review(request_id):
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    user = get_logged_user()
    exchange_request = SkillExchangeRequest.query.get_or_404(request_id)

    if not user_can_access_request(exchange_request, user.id):
        flash("You do not have access to review this exchange.", "danger")
        return redirect(url_for("my_requests"))

    if exchange_request.status != "accepted":
        flash("You can review only accepted exchanges.", "warning")
        return redirect(url_for("my_requests"))

    if has_user_reviewed_request(exchange_request.id, user.id):
        flash("You already reviewed this exchange.", "warning")
        return redirect(url_for("my_reviews"))

    reviewed_user = get_other_user_for_request(exchange_request, user.id)

    if request.method == "POST":
        rating = int(request.form.get("rating", 0))
        review_text = request.form.get("review_text", "").strip()

        if rating < 1 or rating > 5:
            flash("Please select a valid rating between 1 and 5.", "danger")
            return redirect(url_for("add_review", request_id=exchange_request.id))

        review = SkillReview(
            request_id=exchange_request.id,
            reviewer_id=user.id,
            reviewed_user_id=reviewed_user.id,
            rating=rating,
            review_text=review_text
        )

        db.session.add(review)
        db.session.commit()

        flash("Review submitted successfully.", "success")
        return redirect(url_for("my_reviews"))

    return render_template(
        "add_review.html",
        user=user,
        exchange_request=exchange_request,
        reviewed_user=reviewed_user
    )


@app.route("/my-reviews")
def my_reviews():
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    user = get_logged_user()

    received_reviews = SkillReview.query.filter_by(
        reviewed_user_id=user.id
    ).order_by(SkillReview.created_at.desc()).all()

    given_reviews = SkillReview.query.filter_by(
        reviewer_id=user.id
    ).order_by(SkillReview.created_at.desc()).all()

    review_stats = get_review_stats_for_user(user.id)

    return render_template(
        "my_reviews.html",
        user=user,
        received_reviews=received_reviews,
        given_reviews=given_reviews,
        review_stats=review_stats
    )


@app.route("/admin/reviews")
def admin_reviews():
    if not login_required():
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    if not superadmin_required():
        flash("Access denied. Superadmin only.", "danger")
        return redirect(url_for("user_dashboard"))

    rating_filter = request.args.get("rating", "all")

    query = SkillReview.query

    if rating_filter in ["1", "2", "3", "4", "5"]:
        query = query.filter_by(rating=int(rating_filter))

    all_reviews = query.order_by(SkillReview.created_at.desc()).all()
    review_stats = get_platform_review_stats()

    return render_template(
        "admin_reviews.html",
        all_reviews=all_reviews,
        review_stats=review_stats,
        rating_filter=rating_filter
    )


with app.app_context():
    db.create_all()

    admin = User.query.filter_by(email="admin@skillswap.com").first()

    if not admin:
        admin = User(
            full_name="Super Admin",
            email="admin@skillswap.com",
            password=generate_password_hash("admin123"),
            role="superadmin",
            skills_offered="Python, Flask, AI",
            skills_wanted="UI Design, DevOps"
        )

        db.session.add(admin)
        db.session.commit()


if __name__ == "__main__":
    app.run(debug=True)
