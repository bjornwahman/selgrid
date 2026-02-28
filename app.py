import hashlib
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from logging.handlers import RotatingFileHandler

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (
    Flask,
    flash,
    has_request_context,
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
from werkzeug.exceptions import HTTPException
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

LOG_FILE_PATH = BASE_DIR / "selgrid.log"
APP_VERSION_FILE = BASE_DIR / "version.txt"
ERROR_TEMPLATE_PATH = BASE_DIR / "templates" / "500.html"


def is_running_in_docker():
    if Path("/.dockerenv").exists():
        return True

    cgroup_path = Path("/proc/1/cgroup")
    if not cgroup_path.exists():
        return False

    try:
        cgroup_content = cgroup_path.read_text(encoding="utf-8")
    except OSError:
        return False

    return "docker" in cgroup_content or "kubepods" in cgroup_content or "containerd" in cgroup_content


def build_selenium_urls():
    docker_runtime = is_running_in_docker()
    default_grid_host = os.getenv("SELENIUM_GRID_HOST", "selenium-hub" if docker_runtime else "127.0.0.1")
    default_chrome_host = os.getenv("CHROME_SELENIUM_HOST", "chrome-selenium" if docker_runtime else "127.0.0.1")

    return {
        "grid_status": os.getenv("SELENIUM_GRID_STATUS_URL", f"http://{default_grid_host}:4444/status"),
        "chrome_status": os.getenv("CHROME_SELENIUM_STATUS_URL", f"http://{default_chrome_host}:5555/status"),
        "remote": os.getenv("SELENIUM_REMOTE_URL", f"http://{default_grid_host}:4444/wd/hub"),
    }


SELENIUM_URLS = build_selenium_urls()
SELENIUM_GRID_STATUS_URL = SELENIUM_URLS["grid_status"]
CHROME_SELENIUM_STATUS_URL = SELENIUM_URLS["chrome_status"]


def configure_logging():
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler_exists = any(
        isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == str(LOG_FILE_PATH)
        for handler in app.logger.handlers
    )
    if file_handler_exists:
        return

    handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    handler.setLevel(logging.INFO)
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(handler)


configure_logging()
app.logger.info("Selgrid started. Version file: %s", APP_VERSION_FILE)

ALLOWED_EXTENSIONS = {"side"}
SELENIUM_REMOTE_URL = SELENIUM_URLS["remote"]
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
SUPPORTED_COMMAND_OPTIONS = sorted(SUPPORTED_COMMANDS)


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
    token_value = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime)


def ensure_api_token_value_column():
    inspector = db.inspect(db.engine)
    columns = {column["name"] for column in inspector.get_columns("api_token")}
    if "token_value" in columns:
        return

    db.session.execute(db.text("ALTER TABLE api_token ADD COLUMN token_value VARCHAR(255)"))
    db.session.commit()


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


def is_admin_user(user):
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "username", "") == "admin")


def parse_health_payload(payload):
    if not isinstance(payload, dict):
        return False, "okänd payload"

    if "ready" in payload:
        return bool(payload.get("ready")), payload.get("message") or payload.get("state") or ""

    value = payload.get("value")
    if isinstance(value, dict) and "ready" in value:
        return bool(value.get("ready")), value.get("message") or value.get("state") or ""

    if payload.get("status") == "ok":
        return True, "ok"

    return False, "saknar ready/status"


def check_service_health(url: str):
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            body = response.read().decode("utf-8")
            payload = json.loads(body)
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc), "url": url, "details": "kan inte ansluta"}
    except (TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": str(exc), "url": url, "details": "ogiltigt svar"}

    is_ok, details = parse_health_payload(payload)
    return {"ok": is_ok, "url": url, "details": details or "svar mottaget", "payload": payload}


def create_test_case_from_request(owner_id: int):
    upload = request.files.get("side_file")
    side_raw = request.form.get("side_raw", "").strip()
    interval = parse_positive_int(request.form.get("interval_minutes", "5"), 5)
    selected_test = request.form.get("selenium_test_id", "")

    path = None
    source_name = "pasted.side"
    if upload and upload.filename:
        if not is_allowed_file(upload.filename):
            raise ValueError("Endast .side filer stöds")
        source_name = secure_filename(upload.filename)
        path = UPLOAD_DIR / f"{int(time.time())}-{source_name}"
        upload.save(path)
    elif side_raw:
        try:
            json.loads(side_raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Rå .side-data är inte giltig JSON") from exc
        path = UPLOAD_DIR / f"{int(time.time())}-pasted.side"
        path.write_text(side_raw, encoding="utf-8")
    else:
        raise ValueError("Ladda upp en .side-fil eller klistra in rå JSON")

    _, tests, _ = read_side_file(path)
    if not tests:
        raise ValueError("Inga tester hittades i filen")

    default_test = tests[0]
    chosen_test = next((item for item in tests if item.get("id") == selected_test), default_test)
    test_case = TestCase(
        owner_id=owner_id,
        name=chosen_test.get("name") or source_name,
        file_path=str(path),
        interval_minutes=interval,
        selenium_test_id=chosen_test.get("id"),
    )
    db.session.add(test_case)
    db.session.commit()
    schedule_test_case(test_case)

    return test_case


def api_auth_required(func=None):
    """Compatibility decorator for legacy API routes.

    Some deployments still import or reference `@api_auth_required`.
    Keep it defined to avoid NameError during startup even when no API
    endpoints currently use token auth.
    """
    def decorator(inner):
        def wrapped(*args, **kwargs):
            if not has_request_context():
                return inner(*args, **kwargs)

            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Unauthorized"}), 401

            raw_token = auth_header[len("Bearer ") :].strip()
            if not raw_token:
                return jsonify({"error": "Unauthorized"}), 401

            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            token = ApiToken.query.filter_by(token_hash=token_hash).first()
            if not token:
                return jsonify({"error": "Unauthorized"}), 401

            request.api_user = User.query.get(token.owner_id)
            if not request.api_user:
                return jsonify({"error": "Unauthorized"}), 401

            token.last_used_at = datetime.utcnow()
            db.session.commit()
            return inner(*args, **kwargs)

        wrapped.__name__ = inner.__name__
        return wrapped

    if func is None:
        def wrapper(inner):
            return decorator(inner)
        return wrapper
    return decorator(func)


def serialize_test_case(test_case: TestCase):
    latest_run = (
        TestRun.query.filter_by(test_case_id=test_case.id)
        .order_by(TestRun.started_at.desc())
        .first()
    )
    latest_status = latest_run.status if latest_run else "never_run"
    latest_duration = latest_run.total_duration_ms if latest_run else 0
    if latest_status == "failed":
        latest_duration = 0

    return {
        "id": test_case.id,
        "name": test_case.name,
        "active": test_case.active,
        "interval_minutes": test_case.interval_minutes,
        "latest_run": {
            "status": latest_status,
            "duration_ms": latest_duration,
            "error_message": latest_run.error_message if latest_run else None,
            "started_at": latest_run.started_at.isoformat() if latest_run else None,
            "finished_at": latest_run.finished_at.isoformat() if latest_run and latest_run.finished_at else None,
        },
    }


def serialize_test_run_with_metrics(test_run: TestRun):
    metrics = (
        StepMetric.query.filter_by(test_run_id=test_run.id)
        .order_by(StepMetric.step_index.asc())
        .all()
    )
    return {
        "id": test_run.id,
        "status": test_run.status,
        "started_at": test_run.started_at.isoformat() if test_run.started_at else None,
        "finished_at": test_run.finished_at.isoformat() if test_run.finished_at else None,
        "total_duration_ms": test_run.total_duration_ms,
        "error_message": test_run.error_message,
        "metrics": [
            {
                "id": metric.id,
                "step_index": metric.step_index,
                "command": metric.command,
                "target": metric.target,
                "value": metric.value,
                "duration_ms": metric.duration_ms,
                "status": metric.status,
                "error_message": metric.error_message,
            }
            for metric in metrics
        ],
    }


def serialize_latest_test_run_summary(test_run: TestRun | None):
    if not test_run:
        return None

    status = "ok" if test_run.status == "success" else "failed" if test_run.status == "failed" else test_run.status
    return {
        "id": test_run.id,
        "status": status,
        "total_duration_ms": test_run.total_duration_ms,
        "timestamp": test_run.started_at.isoformat() if test_run.started_at else None,
    }


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
        expected_title = value or target
        if driver.title != expected_title:
            raise AssertionError(f"Expected title '{expected_title}', got '{driver.title}'")
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

        status = "success"
        error_message = None
        driver = None
        test_duration_ms = 0

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
                    test_duration_ms += metric.duration_ms
                    db.session.commit()
                    continue
                except Exception as exc:
                    metric.status = "failed"
                    metric.error_message = str(exc)
                    status = "failed"
                    error_message = str(exc)
                    db.session.add(metric)
                    metric.duration_ms = int((time.perf_counter() - step_start) * 1000)
                    test_duration_ms += metric.duration_ms
                    db.session.commit()
                    break

                metric.duration_ms = int((time.perf_counter() - step_start) * 1000)
                test_duration_ms += metric.duration_ms
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
        run.total_duration_ms = test_duration_ms
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
        latest_run = TestRun.query.filter_by(test_case_id=test_case.id).order_by(TestRun.started_at.desc()).first()
        unsupported = []
        try:
            payload, _, _ = read_side_file(Path(test_case.file_path))
            unsupported = get_unsupported_commands(payload, test_case.selenium_test_id)
        except Exception:
            unsupported = ["kunde inte läsa .side"]
        rows.append({"test": test_case, "unsupported": unsupported, "latest_run": latest_run})
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
    test_rows = build_dashboard_rows(current_user.id)
    return render_template("dashboard.html", test_rows=test_rows)


@app.route("/checks", methods=["GET", "POST"])
@login_required
def checks_page():
    if request.method == "POST":
        try:
            test_case = create_test_case_from_request(current_user.id)
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("checks_page"))

        unsupported = get_unsupported_commands(read_side_file(Path(test_case.file_path))[0], test_case.selenium_test_id)
        if unsupported:
            flash(f"Varning: Kommandon som saknas stöd för: {', '.join(unsupported)}")
        flash("Test uppladdat och schemalagt")
        return redirect(url_for("checks_page"))

    test_rows = build_dashboard_rows(current_user.id)
    return render_template("checks.html", test_rows=test_rows)


@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin_page():
    if not is_admin_user(current_user):
        flash("Endast admin har åtkomst till adminsidan")
        return redirect(url_for("dashboard"))

    created_token = None

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "create_user":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not username or not password:
                flash("Användarnamn och lösenord krävs")
            elif User.query.filter_by(username=username).first():
                flash("Användarnamnet finns redan")
            else:
                db.session.add(User(username=username, password_hash=generate_password_hash(password)))
                db.session.commit()
                flash("Användare skapad")

        if action == "update_user":
            user_id = parse_positive_int(request.form.get("user_id"), 0)
            new_username = request.form.get("username", "").strip()
            new_password = request.form.get("password", "")
            user_obj = User.query.get(user_id)
            if not user_obj:
                flash("Användare hittades inte")
            elif not new_username:
                flash("Användarnamn får inte vara tomt")
            elif User.query.filter(User.username == new_username, User.id != user_obj.id).first():
                flash("Användarnamnet används redan")
            else:
                user_obj.username = new_username
                if new_password:
                    user_obj.password_hash = generate_password_hash(new_password)
                db.session.commit()
                flash("Användare uppdaterad")

        if action == "delete_user":
            user_id = parse_positive_int(request.form.get("user_id"), 0)
            user_obj = User.query.get(user_id)
            if not user_obj:
                flash("Användare hittades inte")
            elif user_obj.username == "admin":
                flash("Admin-kontot kan inte raderas")
            else:
                ApiToken.query.filter_by(owner_id=user_obj.id).delete(synchronize_session=False)
                test_cases = TestCase.query.filter_by(owner_id=user_obj.id).all()
                for case in test_cases:
                    unschedule_test_case(case.id)
                    StepMetric.query.filter(
                        StepMetric.test_run_id.in_(db.session.query(TestRun.id).filter_by(test_case_id=case.id))
                    ).delete(synchronize_session=False)
                    TestRun.query.filter_by(test_case_id=case.id).delete(synchronize_session=False)
                    Secret.query.filter_by(test_case_id=case.id).delete(synchronize_session=False)
                    file_path = Path(case.file_path)
                    if file_path.exists():
                        file_path.unlink()
                    db.session.delete(case)
                db.session.delete(user_obj)
                db.session.commit()
                flash("Användare och tillhörande data raderad")

        if action == "create_token":
            owner_id = parse_positive_int(request.form.get("owner_id"), 0)
            name = request.form.get("name", "").strip()
            owner = User.query.get(owner_id)
            if not owner:
                flash("Välj en giltig användare")
            elif not name:
                flash("Token-namn krävs")
            else:
                raw_token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
                token_prefix = raw_token[:8]
                db.session.add(
                    ApiToken(
                        owner_id=owner.id,
                        name=name,
                        token_hash=token_hash,
                        token_prefix=token_prefix,
                        token_value=raw_token,
                    )
                )
                db.session.commit()
                created_token = raw_token
                flash("Token skapad")

        if action == "delete_token":
            token_id = parse_positive_int(request.form.get("token_id"), 0)
            token = ApiToken.query.get(token_id)
            if not token:
                flash("Token hittades inte")
            else:
                db.session.delete(token)
                db.session.commit()
                flash("Token raderad")

    users = User.query.order_by(User.username.asc()).all()
    tokens = ApiToken.query.order_by(ApiToken.created_at.desc()).all()
    user_lookup = {user.id: user.username for user in users}
    return render_template("admin.html", users=users, tokens=tokens, user_lookup=user_lookup, created_token=created_token)


@app.route("/test/<int:test_case_id>/edit", methods=["GET", "POST"], endpoint="edit_test_case")
@login_required
def edit_test_case(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=current_user.id).first_or_404()
    try:
        payload, tests, _ = read_side_file(Path(test_case.file_path))
    except (OSError, JSONDecodeError, ValueError):
        flash("Kunde inte läsa .side-filen för checken")
        return redirect(url_for("dashboard"))

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
        base_url = request.form.get("base_url", "").strip()
        active = request.form.get("active") == "on"

        if not name:
            flash("Namn krävs")
            return redirect(url_for("edit_test_case", test_case_id=test_case.id))

        test_ids = {item.get("id") for item in tests}
        if selenium_test_id and selenium_test_id not in test_ids:
            flash("Valt test finns inte i .side-filen")
            return redirect(url_for("edit_test_case", test_case_id=test_case.id))

        if base_url and not base_url.startswith(("http://", "https://")):
            flash("Bas-URL måste börja med http:// eller https://")
            return redirect(url_for("edit_test_case", test_case_id=test_case.id))

        test_case.name = name
        test_case.interval_minutes = interval_minutes
        if selenium_test_id:
            test_case.selenium_test_id = selenium_test_id
        test_case.active = active
        db.session.commit()

        if base_url:
            payload["urls"] = [base_url]
        else:
            payload["urls"] = []
        write_side_file(Path(test_case.file_path), payload)

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
    base_url = payload.get("urls", [""])[0] if payload.get("urls") else ""
    return render_template(
        "edit_test.html",
        test_case=test_case,
        selenium_tests=tests,
        commands=commands,
        base_url=base_url,
        command_options=SUPPORTED_COMMAND_OPTIONS,
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
    try:
        payload, _, _ = read_side_file(Path(test_case.file_path))
    except (OSError, JSONDecodeError, ValueError):
        flash("Kunde inte läsa .side-filen för checken")
        return redirect(url_for("dashboard"))
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
        chart_durations=[0 if run.status == "failed" else run.total_duration_ms for run in reversed(runs)],
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


@app.route("/docu")
def docu_page():
    return render_template("docs.html")


@app.route("/docu/openapi.json")
def docu_openapi():
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
                "/api/tests/{id}/results": {
                    "get": {
                        "summary": "Visa senaste körningen för en check",
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Senaste körning"}, "404": {"description": "Not found"}},
                    }
                },
            },
        }
    )


@app.route("/status")
@login_required
def status_page():
    grid_health = check_service_health(SELENIUM_GRID_STATUS_URL)
    chrome_health = check_service_health(CHROME_SELENIUM_STATUS_URL)
    return render_template("status.html", grid_health=grid_health, chrome_health=chrome_health)


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


@app.route("/api/tests/<int:test_case_id>/results", methods=["GET"])
@api_auth_required
def api_test_results(test_case_id):
    test_case = TestCase.query.filter_by(id=test_case_id, owner_id=request.api_user.id).first()
    if not test_case:
        return jsonify({"error": "Test not found"}), 404

    latest_run = (
        TestRun.query.filter_by(test_case_id=test_case.id)
        .order_by(TestRun.started_at.desc())
        .first()
    )
    return jsonify(
        {
            "test_id": test_case.id,
            "test_name": test_case.name,
            "latest_result": serialize_latest_test_run_summary(latest_run),
        }
    )


@app.route("/uploads/<path:filename>")
@login_required
def get_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if isinstance(error, HTTPException):
        return error

    app.logger.exception("Unhandled application error", exc_info=error)
    if ERROR_TEMPLATE_PATH.exists():
        return render_template("500.html"), 500
    return (
        "Internal Server Error. Se selgrid.log i projektets rot för detaljer.",
        500,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


with app.app_context():
    db.create_all()
    ensure_api_token_value_column()
    ensure_default_admin_user()
    if not scheduler.running:
        scheduler.start()
    for case in TestCase.query.filter_by(active=True).all():
        schedule_test_case(case)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
