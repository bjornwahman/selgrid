import json
import os
import time
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("APP_SECRET", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", f"sqlite:///{BASE_DIR / 'selgrid.db'}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

ALLOWED_EXTENSIONS = {"side"}
SELENIUM_REMOTE_URL = os.getenv("SELENIUM_REMOTE_URL", "http://127.0.0.1:4444/wd/hub")

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
scheduler = BackgroundScheduler()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class TestCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    interval_minutes = db.Column(db.Integer, nullable=False)
    selenium_test_id = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean, default=True)


class Secret(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    test_case_id = db.Column(db.Integer, db.ForeignKey("test_case.id"), nullable=False)
    key = db.Column(db.String(255), nullable=False)
    value = db.Column(db.String(1024), nullable=False)


class TestRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    test_case_id = db.Column(db.Integer, db.ForeignKey("test_case.id"), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False)
    finished_at = db.Column(db.DateTime)
    status = db.Column(db.String(30), nullable=False)
    total_duration_ms = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)


class StepMetric(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    test_run_id = db.Column(db.Integer, db.ForeignKey("test_run.id"), nullable=False)
    step_index = db.Column(db.Integer, nullable=False)
    command = db.Column(db.String(100), nullable=False)
    target = db.Column(db.String(500))
    value = db.Column(db.String(500))
    duration_ms = db.Column(db.Integer, default=0)
    status = db.Column(db.String(30), nullable=False)
    error_message = db.Column(db.Text)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def is_allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def read_side_file(path: Path):
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    tests = payload.get("tests", [])
    urls = payload.get("urls", [])
    return payload, tests, urls


def get_secrets_map(test_case_id: int):
    secrets = Secret.query.filter_by(test_case_id=test_case_id).all()
    return {item.key: item.value for item in secrets}


def replace_secret(value: str, secrets: dict):
    if not value:
        return value
    for key, secret in secrets.items():
        value = value.replace(f"${{{key}}}", secret)
    return value


def perform_command(driver, command, target, value):
    if command == "open":
        driver.get(target)
    elif command == "click":
        driver.find_element(By.CSS_SELECTOR, target).click()
    elif command == "type":
        elem = driver.find_element(By.CSS_SELECTOR, target)
        elem.clear()
        elem.send_keys(value)
    elif command == "assertTitle":
        if driver.title != value:
            raise AssertionError(f"Expected title '{value}', got '{driver.title}'")
    else:
        raise NotImplementedError(f"Command '{command}' is not supported yet")


def run_test_case(test_case_id: int):
    with app.app_context():
        test_case = TestCase.query.get(test_case_id)
        if not test_case or not test_case.active:
            return

        run = TestRun(
            test_case_id=test_case.id,
            started_at=datetime.utcnow(),
            status="running",
        )
        db.session.add(run)
        db.session.commit()

        start = time.perf_counter()
        status = "success"
        error_message = None
        driver = None

        try:
            payload, tests, urls = read_side_file(Path(test_case.file_path))
            selenium_test = next(
                (t for t in tests if t.get("id") == test_case.selenium_test_id), None
            )
            if not selenium_test:
                raise ValueError("Test not found in .side file")

            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            driver = webdriver.Remote(command_executor=SELENIUM_REMOTE_URL, options=options)

            secrets = get_secrets_map(test_case.id)
            base_url = urls[0] if urls else ""
            if base_url:
                driver.get(base_url)

            for idx, step in enumerate(selenium_test.get("commands", []), start=1):
                step_start = time.perf_counter()
                command = step.get("command", "")
                target = replace_secret(step.get("target", ""), secrets)
                value = replace_secret(step.get("value", ""), secrets)
                metric = StepMetric(
                    test_run_id=run.id,
                    step_index=idx,
                    command=command,
                    target=target,
                    value=value,
                    status="success",
                )
                try:
                    if command == "open" and base_url and not target.startswith("http"):
                        target = f"{base_url.rstrip('/')}/{target.lstrip('/')}"
                    perform_command(driver, command, target, value)
                except Exception as exc:
                    metric.status = "failed"
                    metric.error_message = str(exc)
                    status = "failed"
                    error_message = str(exc)
                    db.session.add(metric)
                    metric.duration_ms = int((time.perf_counter() - step_start) * 1000)
                    db.session.commit()
                    break
                metric.duration_ms = int((time.perf_counter() - step_start) * 1000)
                db.session.add(metric)
                db.session.commit()

        except (ValueError, WebDriverException) as exc:
            status = "failed"
            error_message = str(exc)
        finally:
            if driver:
                driver.quit()

        run.status = status
        run.error_message = error_message
        run.finished_at = datetime.utcnow()
        run.total_duration_ms = int((time.perf_counter() - start) * 1000)
        db.session.commit()


def schedule_test_case(test_case: TestCase):
    scheduler.add_job(
        run_test_case,
        "interval",
        minutes=test_case.interval_minutes,
        args=[test_case.id],
        id=f"test-{test_case.id}",
        replace_existing=True,
    )


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Användarnamn och lösenord krävs")
            return redirect(url_for("register"))
        if User.query.filter_by(username=username).first():
            flash("Användarnamnet är redan upptaget")
            return redirect(url_for("register"))
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Fel användarnamn eller lösenord")
            return redirect(url_for("login"))
        login_user(user)
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    if request.method == "POST":
        upload = request.files.get("side_file")
        interval = int(request.form.get("interval_minutes", "5"))
        selected_test = request.form.get("selenium_test_id", "")

        if not upload or upload.filename == "":
            flash("Du måste välja en .side fil")
            return redirect(url_for("dashboard"))

        if not is_allowed_file(upload.filename):
            flash("Endast .side filer stöds")
            return redirect(url_for("dashboard"))

        filename = secure_filename(upload.filename)
        path = UPLOAD_DIR / f"{int(time.time())}-{filename}"
        upload.save(path)

        _, tests, _ = read_side_file(path)
        if not tests:
            flash("Inga tester hittades i filen")
            return redirect(url_for("dashboard"))

        test_id = selected_test or tests[0].get("id")
        test_name = next((t.get("name") for t in tests if t.get("id") == test_id), tests[0].get("name"))

        test_case = TestCase(
            owner_id=current_user.id,
            name=test_name,
            file_path=str(path),
            interval_minutes=max(interval, 1),
            selenium_test_id=test_id,
        )
        db.session.add(test_case)
        db.session.commit()
        schedule_test_case(test_case)
        flash("Test uppladdat och schemalagt")
        return redirect(url_for("dashboard"))

    tests = TestCase.query.filter_by(owner_id=current_user.id).all()
    return render_template("dashboard.html", tests=tests)


@app.route("/test/<int:test_case_id>")
@login_required
def test_detail(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=current_user.id).first_or_404()
    runs = TestRun.query.filter_by(test_case_id=test_case.id).order_by(TestRun.started_at.desc()).all()
    metrics = {
        run.id: StepMetric.query.filter_by(test_run_id=run.id)
        .order_by(StepMetric.step_index.asc())
        .all()
        for run in runs
    }
    secrets = Secret.query.filter_by(test_case_id=test_case.id).all()
    return render_template(
        "test_detail.html", test_case=test_case, runs=runs, metrics=metrics, secrets=secrets
    )


@app.route("/test/<int:test_case_id>/run", methods=["POST"])
@login_required
def run_now(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=current_user.id).first_or_404()
    run_test_case(test_case.id)
    flash("Test körning startad")
    return redirect(url_for("test_detail", test_case_id=test_case.id))


@app.route("/test/<int:test_case_id>/secret", methods=["POST"])
@login_required
def add_secret(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=current_user.id).first_or_404()
    key = request.form.get("key", "").strip()
    value = request.form.get("value", "").strip()
    if not key or not value:
        flash("Både nyckel och värde krävs")
        return redirect(url_for("test_detail", test_case_id=test_case.id))

    secret = Secret.query.filter_by(test_case_id=test_case.id, key=key).first()
    if secret:
        secret.value = value
    else:
        db.session.add(Secret(test_case_id=test_case.id, key=key, value=value))
    db.session.commit()
    flash("Secret sparad")
    return redirect(url_for("test_detail", test_case_id=test_case.id))


@app.route("/uploads/<path:filename>")
@login_required
def get_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


with app.app_context():
    db.create_all()
    if not scheduler.running:
        scheduler.start()
    for case in TestCase.query.filter_by(active=True).all():
        schedule_test_case(case)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
