import hashlib
import json
import os
import secrets
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (
    Flask,
    flash,
    jsonify,
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
from selenium.webdriver import ActionChains
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
DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "").strip()
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "").strip()
SUPPORTED_COMMANDS = {
    "open",
    "click",
    "doubleClick",
    "type",
    "sendKeys",
    "select",
    "check",
    "uncheck",
    "mouseOver",
    "submit",
    "pause",
    "assertTitle",
    "assertText",
    "assertValue",
    "assertElementPresent",
    "assertElementNotPresent",
    "waitForElementPresent",
    "waitForElementVisible",
    "waitForElement",
    "waitForElementNotPresent",
    "waitForElementNotVisible",
    "setWindowSize",
    "comment",
    "echo",
    "note",
}


db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
scheduler = BackgroundScheduler()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class ApiToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    token_prefix = db.Column(db.String(12), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime)


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


def ensure_default_admin_user():
    if not DEFAULT_ADMIN_USERNAME or not DEFAULT_ADMIN_PASSWORD:
        return

    admin_user = User.query.filter_by(username=DEFAULT_ADMIN_USERNAME).first()
    if not admin_user:
        db.session.add(
            User(
                username=DEFAULT_ADMIN_USERNAME,
                password_hash=generate_password_hash(DEFAULT_ADMIN_PASSWORD),
            )
        )
        db.session.commit()


def is_allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def read_side_file(path: Path):
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload, payload.get("tests", []), payload.get("urls", [])


def write_side_file(path: Path, payload: dict):
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def get_test_commands(payload: dict, selenium_test_id: str):
    selenium_test = next((t for t in payload.get("tests", []) if t.get("id") == selenium_test_id), None)
    if not selenium_test:
        raise ValueError("Test not found in .side file")
    return selenium_test.get("commands", [])


def get_unsupported_commands(payload: dict, selenium_test_id: str):
    commands = get_test_commands(payload, selenium_test_id)
    unsupported = sorted(
        {step.get("command", "") for step in commands if step.get("command", "") not in SUPPORTED_COMMANDS}
    )
    return unsupported


def get_secrets_map(test_case_id: int):
    secrets_list = Secret.query.filter_by(test_case_id=test_case_id).all()
    return {item.key: item.value for item in secrets_list}


def replace_secret(value: str, secrets_map: dict):
    if not value:
        return value
    for key, secret in secrets_map.items():
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


def parse_positive_int(raw_value, default_value):
    try:
        return max(int(raw_value), 1)
    except (TypeError, ValueError):
        return default_value


def api_auth_required(func=None):
    """Compatibility decorator for legacy API routes.

    Some deployments still import or reference `@api_auth_required`.
    Keep it defined to avoid NameError during startup even when no API
    endpoints currently use token auth.
    """
    if func is None:
        def wrapper(inner):
            return inner
        return wrapper
    return func


def perform_command(driver, command, target, value):
    if command in {"comment", "echo", "note"}:
        return
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
        elem.send_keys(resolve_key_token(value))
    elif command == "select":
        by, selector = resolve_locator(target)
        dropdown = Select(driver.find_element(by, selector))
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
        ActionChains(driver).move_to_element(driver.find_element(by, selector)).perform()
    elif command == "submit":
        by, selector = resolve_locator(target)
        driver.find_element(by, selector).submit()
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
    elif command == "assertElementNotPresent":
        by, selector = resolve_locator(target)
        if driver.find_elements(by, selector):
            raise AssertionError(f"Element should not exist: {target}")
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
        WebDriverWait(driver, timeout_s).until_not(EC.presence_of_element_located((by, selector)))
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

        run = TestRun(test_case_id=test_case.id, started_at=datetime.utcnow(), status="running")
        db.session.add(run)
        db.session.commit()

        start = time.perf_counter()
        status = "success"
        error_message = None
        driver = None

        try:
            payload, tests, urls = read_side_file(Path(test_case.file_path))
            selenium_test = next((t for t in tests if t.get("id") == test_case.selenium_test_id), None)
            if not selenium_test:
                raise ValueError("Test not found in .side file")

            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            driver = webdriver.Remote(command_executor=SELENIUM_REMOTE_URL, options=options)

            secrets_map = get_secrets_map(test_case.id)
            base_url = urls[0] if urls else ""
            if base_url:
                driver.get(base_url)

            for idx, step in enumerate(selenium_test.get("commands", []), start=1):
                step_start = time.perf_counter()
                command = step.get("command", "")
                target = replace_secret(step.get("target", ""), secrets_map)
                value = replace_secret(step.get("value", ""), secrets_map)
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
                except NotImplementedError as exc:
                    metric.status = "warning"
                    metric.error_message = str(exc)
                    db.session.add(metric)
                    metric.duration_ms = int((time.perf_counter() - step_start) * 1000)
                    db.session.commit()
                    continue
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


def build_dashboard_rows(owner_id: int):
    rows = []
    tests = TestCase.query.filter_by(owner_id=owner_id).order_by(TestCase.id.desc()).all()
    for test_case in tests:
        unsupported = []
        try:
            payload, _, _ = read_side_file(Path(test_case.file_path))
            unsupported = get_unsupported_commands(payload, test_case.selenium_test_id)
        except Exception:
            unsupported = ["kunde inte läsa .side"]
        rows.append({"test": test_case, "unsupported": unsupported})
    return rows


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    flash("Självregistrering är avstängd. Logga in med admin-kontot.")
    return redirect(url_for("login"))


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
        side_raw = request.form.get("side_raw", "").strip()
        interval = parse_positive_int(request.form.get("interval_minutes", "5"), 5)
        selected_test = request.form.get("selenium_test_id", "")

        path = None
        if upload and upload.filename:
            if not is_allowed_file(upload.filename):
                flash("Endast .side filer stöds")
                return redirect(url_for("dashboard"))
            filename = secure_filename(upload.filename)
            path = UPLOAD_DIR / f"{int(time.time())}-{filename}"
            upload.save(path)
        elif side_raw:
            try:
                json.loads(side_raw)
            except json.JSONDecodeError:
                flash("Rå .side-data är inte giltig JSON")
                return redirect(url_for("dashboard"))
            path = UPLOAD_DIR / f"{int(time.time())}-pasted.side"
            path.write_text(side_raw, encoding="utf-8")
        else:
            flash("Ladda upp en .side-fil eller klistra in rå JSON")
            return redirect(url_for("dashboard"))

        _, tests, _ = read_side_file(path)
        if not tests:
            flash("Inga tester hittades i filen")
            return redirect(url_for("dashboard"))

        default_test = tests[0]
        test_case = TestCase(
            owner_id=current_user.id,
            name=default_test.get("name", filename),
            file_path=str(path),
            interval_minutes=interval,
            selenium_test_id=test_id,
        )
        db.session.add(test_case)
        db.session.commit()
        schedule_test_case(test_case)

        unsupported = get_unsupported_commands(read_side_file(path)[0], test_case.selenium_test_id)
        if unsupported:
            flash(f"Varning: Kommandon som saknas stöd för: {', '.join(unsupported)}")

        flash("Test uppladdat och schemalagt")
        return redirect(url_for("dashboard"))

    test_rows = build_dashboard_rows(current_user.id)
    return render_template("dashboard.html", test_rows=test_rows)


@app.route("/test/<int:test_case_id>/edit", methods=["GET", "POST"], endpoint="edit_test_case")
@login_required
def edit_test_case(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=current_user.id).first_or_404()
    payload, tests, _ = read_side_file(Path(test_case.file_path))

    if request.method == "POST":
        form_action = request.form.get("action", "update_meta")

        if form_action == "update_side":
            commands = []
            command_values = request.form.getlist("command[]")
            target_values = request.form.getlist("target[]")
            value_values = request.form.getlist("value[]")
            for command, target, value in zip(command_values, target_values, value_values):
                if not command.strip() and not target.strip() and not value.strip():
                    continue
                commands.append(
                    {
                        "id": f"cmd-{int(time.time() * 1000)}-{len(commands)}",
                        "command": command.strip(),
                        "target": target.strip(),
                        "value": value.strip(),
                    }
                )

            selenium_test = next(
                (item for item in payload.get("tests", []) if item.get("id") == test_case.selenium_test_id),
                None,
            )
            if not selenium_test:
                flash("Kunde inte hitta valt test i .side-fil")
                return redirect(url_for("edit_test_case", test_case_id=test_case.id))

            selenium_test["commands"] = commands
            write_side_file(Path(test_case.file_path), payload)
            unsupported = get_unsupported_commands(payload, test_case.selenium_test_id)
            if unsupported:
                flash(f"Varning: Kommandon som saknas stöd för: {', '.join(unsupported)}")
            flash(".side-tabellen uppdaterad")
            return redirect(url_for("edit_test_case", test_case_id=test_case.id))

        if form_action == "add_note":
            note_text = request.form.get("note_text", "").strip()
            if note_text:
                selenium_test = next(
                    (item for item in payload.get("tests", []) if item.get("id") == test_case.selenium_test_id),
                    None,
                )
                if selenium_test is not None:
                    selenium_test.setdefault("commands", []).append(
                        {
                            "id": f"note-{int(time.time() * 1000)}",
                            "command": "comment",
                            "target": "",
                            "value": note_text,
                        }
                    )
                    write_side_file(Path(test_case.file_path), payload)
            flash("Textnotering tillagd i testet")
            return redirect(url_for("edit_test_case", test_case_id=test_case.id))

        name = request.form.get("name", "").strip()
        interval_minutes = parse_positive_int(
            request.form.get("interval_minutes", test_case.interval_minutes), test_case.interval_minutes
        )
        selenium_test_id = request.form.get("selenium_test_id", "").strip()
        active = request.form.get("active") == "on"

        if not name:
            flash("Namn krävs")
            return redirect(url_for("edit_test_case", test_case_id=test_case.id))

        test_ids = {item.get("id") for item in tests}
        if selenium_test_id and selenium_test_id not in test_ids:
            flash("Valt test finns inte i .side-filen")
            return redirect(url_for("edit_test_case", test_case_id=test_case.id))

        test_case.name = name
        test_case.interval_minutes = interval_minutes
        if selenium_test_id:
            test_case.selenium_test_id = selenium_test_id
        test_case.active = active
        db.session.commit()

        if test_case.active:
            schedule_test_case(test_case)
        else:
            unschedule_test_case(test_case.id)

        unsupported = get_unsupported_commands(read_side_file(Path(test_case.file_path))[0], test_case.selenium_test_id)
        if unsupported:
            flash(f"Varning: Kommandon som saknas stöd för: {', '.join(unsupported)}")

        flash("Test uppdaterat")
        return redirect(url_for("test_detail", test_case_id=test_case.id))

    selected_test = next((item for item in tests if item.get("id") == test_case.selenium_test_id), None)
    commands = selected_test.get("commands", []) if selected_test else []
    unsupported = get_unsupported_commands(payload, test_case.selenium_test_id)
    return render_template(
        "edit_test.html",
        test_case=test_case,
        selenium_tests=tests,
        commands=commands,
        unsupported_commands=unsupported,
    )


@app.route("/test/<int:test_case_id>/delete", methods=["POST"])
@login_required
def delete_test(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=current_user.id).first_or_404()
    unschedule_test_case(test_case.id)

    StepMetric.query.filter(
        StepMetric.test_run_id.in_(db.session.query(TestRun.id).filter_by(test_case_id=test_case.id))
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
        run.id: StepMetric.query.filter_by(test_run_id=run.id).order_by(StepMetric.step_index.asc()).all()
        for run in runs
    }
    payload, _, _ = read_side_file(Path(test_case.file_path))
    unsupported = get_unsupported_commands(payload, test_case.selenium_test_id)
    total_runs = len(runs)
    success_runs = len([item for item in runs if item.status == "success"])
    warning_steps = sum(len([step for step in metrics[item.id] if step.status == "warning"]) for item in runs)
    avg_duration = int(sum(item.total_duration_ms for item in runs) / total_runs) if total_runs else 0

    return render_template(
        "test_detail.html",
        test_case=test_case,
        runs=runs,
        metrics=metrics,
        secrets=Secret.query.filter_by(test_case_id=test_case.id).all(),
        unsupported_commands=unsupported,
        chart_labels=[run.started_at.strftime("%Y-%m-%d %H:%M:%S") for run in reversed(runs)],
        chart_durations=[run.total_duration_ms for run in reversed(runs)],
        chart_statuses=[run.status for run in reversed(runs)],
        total_runs=total_runs,
        success_rate=int((success_runs / total_runs) * 100) if total_runs else 0,
        avg_duration=avg_duration,
        warning_steps=warning_steps,
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

    secret_obj = Secret.query.filter_by(test_case_id=test_case.id, key=key).first()
    if secret_obj:
        secret_obj.value = value
    else:
        db.session.add(Secret(test_case_id=test_case.id, key=key, value=value))
    db.session.commit()
    flash("Secret sparad")
    return redirect(url_for("test_detail", test_case_id=test_case.id))


@app.route("/docs")
def docs_page():
    return render_template("docs.html")


@app.route("/docs/openapi.json")
def docs_openapi():
    return jsonify(
        {
            "openapi": "3.0.3",
            "info": {"title": "Selgrid API", "version": "1.0.0"},
            "components": {
                "securitySchemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "API token"}
                }
            },
            "security": [{"bearerAuth": []}],
            "paths": {
                "/api/health": {"get": {"summary": "Healthcheck", "responses": {"200": {"description": "OK"}}}},
                "/api/tests": {
                    "get": {
                        "summary": "Lista checkar",
                        "responses": {"200": {"description": "Lista"}, "401": {"description": "Unauthorized"}},
                    }
                },
                "/api/tests/{id}/run": {
                    "post": {
                        "summary": "Kör check nu",
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Started"}, "404": {"description": "Not found"}},
                    }
                },
            },
        }
    )


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


@app.route("/api/tests", methods=["GET"])
@api_auth_required
def api_tests():
    tests = TestCase.query.filter_by(owner_id=request.api_user.id).order_by(TestCase.id.desc()).all()
    return jsonify([serialize_test_case(test_case) for test_case in tests])


@app.route("/api/tests/<int:test_case_id>/run", methods=["POST"])
@api_auth_required
def api_run_now(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=request.api_user.id).first()
    if not test_case:
        return jsonify({"error": "Test not found"}), 404
    run_test_case(test_case.id)
    return jsonify({"message": "Run started", "test_id": test_case.id})


@app.route("/uploads/<path:filename>")
@login_required
def get_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


with app.app_context():
    db.create_all()
    ensure_default_admin_user()
    if not scheduler.running:
        scheduler.start()
    for case in TestCase.query.filter_by(active=True).all():
        schedule_test_case(case)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
