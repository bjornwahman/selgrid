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
from selenium.webdriver import ActionChains
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
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


def resolve_locator(target: str):
    if not target:
        raise ValueError("Missing target for locator-based command")

    if target.startswith("xpath="):
        return By.XPATH, target[len("xpath=") :]
    if target.startswith("css="):
        return By.CSS_SELECTOR, target[len("css=") :]
    if target.startswith("id="):
        return By.ID, target[len("id=") :]
    if target.startswith("name="):
        return By.NAME, target[len("name=") :]
    if target.startswith("linkText="):
        return By.LINK_TEXT, target[len("linkText=") :]
    if target.startswith("partialLinkText="):
        return By.PARTIAL_LINK_TEXT, target[len("partialLinkText=") :]
    if target.startswith("class="):
        return By.CLASS_NAME, target[len("class=") :]
    if target.startswith("tag="):
        return By.TAG_NAME, target[len("tag=") :]

    return By.CSS_SELECTOR, target


def resolve_key_token(key_name: str):
    mapping = {
        "ENTER": Keys.ENTER,
        "TAB": Keys.TAB,
        "ESCAPE": Keys.ESCAPE,
        "SPACE": Keys.SPACE,
        "BACKSPACE": Keys.BACKSPACE,
        "DELETE": Keys.DELETE,
        "ARROW_UP": Keys.ARROW_UP,
        "ARROW_DOWN": Keys.ARROW_DOWN,
        "ARROW_LEFT": Keys.ARROW_LEFT,
        "ARROW_RIGHT": Keys.ARROW_RIGHT,
    }
    return mapping.get(key_name.upper(), key_name)


def perform_command(driver, command, target, value):
    if command == "open":
        driver.get(target)
    elif command == "click":
        by, selector = resolve_locator(target)
        driver.find_element(by, selector).click()
    elif command == "doubleClick":
        by, selector = resolve_locator(target)
        elem = driver.find_element(by, selector)
        ActionChains(driver).double_click(elem).perform()
    elif command == "type":
        by, selector = resolve_locator(target)
        elem = driver.find_element(by, selector)
        elem.clear()
        elem.send_keys(value)
    elif command == "sendKeys":
        by, selector = resolve_locator(target)
        elem = driver.find_element(by, selector)
        token = resolve_key_token(value)
        elem.send_keys(token)
    elif command == "select":
        by, selector = resolve_locator(target)
        elem = driver.find_element(by, selector)
        dropdown = Select(elem)
        if value.startswith("label="):
            dropdown.select_by_visible_text(value[len("label=") :])
        elif value.startswith("value="):
            dropdown.select_by_value(value[len("value=") :])
        elif value.startswith("index="):
            dropdown.select_by_index(int(value[len("index=") :]))
        else:
            dropdown.select_by_visible_text(value)
    elif command == "check":
        by, selector = resolve_locator(target)
        elem = driver.find_element(by, selector)
        if not elem.is_selected():
            elem.click()
    elif command == "uncheck":
        by, selector = resolve_locator(target)
        elem = driver.find_element(by, selector)
        if elem.is_selected():
            elem.click()
    elif command == "mouseOver":
        by, selector = resolve_locator(target)
        elem = driver.find_element(by, selector)
        ActionChains(driver).move_to_element(elem).perform()
    elif command == "submit":
        by, selector = resolve_locator(target)
        elem = driver.find_element(by, selector)
        elem.submit()
    elif command == "pause":
        delay_ms = int(target or value or "0")
        time.sleep(max(delay_ms, 0) / 1000)
    elif command == "assertTitle":
        if driver.title != value:
            raise AssertionError(f"Expected title '{value}', got '{driver.title}'")
    elif command == "assertText":
        by, selector = resolve_locator(target)
        actual = driver.find_element(by, selector).text
        if actual != value:
            raise AssertionError(f"Expected text '{value}', got '{actual}'")
    elif command == "assertValue":
        by, selector = resolve_locator(target)
        actual = driver.find_element(by, selector).get_attribute("value")
        if actual != value:
            raise AssertionError(f"Expected value '{value}', got '{actual}'")
    elif command == "assertElementPresent":
        by, selector = resolve_locator(target)
        if not driver.find_elements(by, selector):
            raise AssertionError(f"Element not found: {target}")
    elif command == "waitForElementPresent":
        by, selector = resolve_locator(target)
        timeout_s = int(value) if value else 10
        WebDriverWait(driver, timeout_s).until(EC.presence_of_element_located((by, selector)))
    elif command in {"waitForElementVisible", "waitForElement"}:
        by, selector = resolve_locator(target)
        timeout_s = int(value) if value else 10
        WebDriverWait(driver, timeout_s).until(EC.visibility_of_element_located((by, selector)))
    elif command in {"waitForElementNotPresent", "waitForElementNotVisible"}:
        by, selector = resolve_locator(target)
        timeout_s = int(value) if value else 10
        WebDriverWait(driver, timeout_s).until_not(
            EC.presence_of_element_located((by, selector))
        )
    elif command == "assertElementNotPresent":
        by, selector = resolve_locator(target)
        if driver.find_elements(by, selector):
            raise AssertionError(f"Element should not exist: {target}")
    elif command == "setWindowSize":
        width, height = (value or target).split("x")
        driver.set_window_size(int(width), int(height))
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


def unschedule_test_case(test_case_id: int):
    job_id = f"test-{test_case_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


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

    tests = TestCase.query.filter_by(owner_id=current_user.id).order_by(TestCase.id.desc()).all()
    return render_template("dashboard.html", tests=tests)


@app.route("/test/<int:test_case_id>/edit", methods=["GET", "POST"])
@login_required
def edit_test(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=current_user.id).first_or_404()
    _, tests, _ = read_side_file(Path(test_case.file_path))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        interval_minutes = int(request.form.get("interval_minutes", test_case.interval_minutes))
        selenium_test_id = request.form.get("selenium_test_id", "").strip()
        active = request.form.get("active") == "on"

        if not name:
            flash("Namn krävs")
            return redirect(url_for("edit_test", test_case_id=test_case.id))

        test_ids = {item.get("id") for item in tests}
        if selenium_test_id and selenium_test_id not in test_ids:
            flash("Valt test finns inte i .side-filen")
            return redirect(url_for("edit_test", test_case_id=test_case.id))

        test_case.name = name
        test_case.interval_minutes = max(interval_minutes, 1)
        if selenium_test_id:
            test_case.selenium_test_id = selenium_test_id
        test_case.active = active
        db.session.commit()

        if test_case.active:
            schedule_test_case(test_case)
        else:
            unschedule_test_case(test_case.id)

        flash("Test uppdaterat")
        return redirect(url_for("test_detail", test_case_id=test_case.id))

    return render_template("edit_test.html", test_case=test_case, selenium_tests=tests)


@app.route("/test/<int:test_case_id>/delete", methods=["POST"])
@login_required
def delete_test(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=current_user.id).first_or_404()
    unschedule_test_case(test_case.id)

    StepMetric.query.filter(
        StepMetric.test_run_id.in_(
            db.session.query(TestRun.id).filter_by(test_case_id=test_case.id)
        )
    ).delete(synchronize_session=False)
    TestRun.query.filter_by(test_case_id=test_case.id).delete(synchronize_session=False)
    Secret.query.filter_by(test_case_id=test_case.id).delete(synchronize_session=False)

    file_path = Path(test_case.file_path)
    db.session.delete(test_case)
    db.session.commit()

    if file_path.exists():
        file_path.unlink()

    flash("Test borttaget")
    return redirect(url_for("dashboard"))


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
        "test_detail.html",
        test_case=test_case,
        runs=runs,
        metrics=metrics,
        secrets=secrets,
        chart_labels=[run.started_at.strftime("%Y-%m-%d %H:%M:%S") for run in reversed(runs)],
        chart_durations=[run.total_duration_ms for run in reversed(runs)],
        chart_statuses=[run.status for run in reversed(runs)],
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
