import io
import json
from pathlib import Path

import app as selgrid_app


def reset_db():
    with selgrid_app.app.app_context():
        selgrid_app.db.drop_all()
        selgrid_app.db.create_all()


def register_and_login(client):
    with selgrid_app.app.app_context():
        selgrid_app.db.session.add(
            selgrid_app.User(
                username="anna",
                password_hash=selgrid_app.generate_password_hash("hemligt"),
            )
        )
        selgrid_app.db.session.commit()
    client.post("/login", data={"username": "anna", "password": "hemligt"}, follow_redirects=True)


def build_side_payload():
    return {
        "id": "project-1",
        "version": "2.0",
        "name": "demo",
        "urls": ["https://example.com"],
        "tests": [
            {
                "id": "t-1",
                "name": "mitt test",
                "commands": [
                    {"command": "open", "target": "/", "value": ""},
                    {"command": "waitForElement", "target": "css=.hero", "value": "5"},
                ],
            }
        ],
    }


def create_api_token_for_user(user_id, name="apitoken"):
    raw_token = "token-123"
    token_hash = selgrid_app.hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    selgrid_app.db.session.add(
        selgrid_app.ApiToken(
            owner_id=user_id,
            name=name,
            token_hash=token_hash,
            token_prefix=raw_token[:8],
        )
    )
    selgrid_app.db.session.commit()
    return raw_token


def test_secret_replacement():
    out = selgrid_app.replace_secret("hello ${USER}", {"USER": "world"})
    assert out == "hello world"


def test_runtime_variable_replacement():
    out = selgrid_app.replace_runtime_variables("Hej ${name}", {"name": "Anna"})
    assert out == "Hej Anna"


def test_build_selenium_urls_uses_docker_service_names(monkeypatch):
    monkeypatch.setattr(selgrid_app, "is_running_in_docker", lambda: True)
    monkeypatch.delenv("SELENIUM_GRID_HOST", raising=False)
    monkeypatch.delenv("CHROME_SELENIUM_HOST", raising=False)
    monkeypatch.delenv("SELENIUM_GRID_STATUS_URL", raising=False)
    monkeypatch.delenv("CHROME_SELENIUM_STATUS_URL", raising=False)
    monkeypatch.delenv("SELENIUM_REMOTE_URL", raising=False)

    urls = selgrid_app.build_selenium_urls()

    assert urls["grid_status"] == "http://selenium-hub:4444/status"
    assert urls["chrome_status"] == "http://chrome-selenium:5555/status"
    assert urls["remote"] == "http://selenium-hub:4444/wd/hub"


def test_build_selenium_urls_prefers_explicit_env(monkeypatch):
    monkeypatch.setattr(selgrid_app, "is_running_in_docker", lambda: True)
    monkeypatch.setenv("SELENIUM_GRID_HOST", "grid")
    monkeypatch.setenv("CHROME_SELENIUM_HOST", "chrome")
    monkeypatch.setenv("SELENIUM_GRID_STATUS_URL", "http://custom-grid/status")
    monkeypatch.setenv("CHROME_SELENIUM_STATUS_URL", "http://custom-chrome/status")
    monkeypatch.setenv("SELENIUM_REMOTE_URL", "http://custom-grid/wd/hub")

    urls = selgrid_app.build_selenium_urls()

    assert urls["grid_status"] == "http://custom-grid/status"
    assert urls["chrome_status"] == "http://custom-chrome/status"
    assert urls["remote"] == "http://custom-grid/wd/hub"


def test_upload_via_file_and_raw_json():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    payload = build_side_payload()
    file_data = {
        "interval_minutes": "5",
        "side_file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "demo.side"),
    }
    response = client.post("/checks", data=file_data, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"mitt test" in response.data

    raw_data = {
        "interval_minutes": "3",
        "side_raw": json.dumps(payload),
    }
    raw_response = client.post("/checks", data=raw_data, follow_redirects=True)
    assert raw_response.status_code == 200


def test_edit_table_and_add_note():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    payload = build_side_payload()
    client.post(
        "/checks",
        data={
            "interval_minutes": "5",
            "side_file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "demo.side"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with selgrid_app.app.app_context():
        case = selgrid_app.TestCase.query.first()
        case_id = case.id
        file_path = Path(case.file_path)

    edit_view = client.get(f"/test/{case_id}/edit", follow_redirects=True)
    assert b"waitForElement" in edit_view.data
    assert b"L\xc3\xa4gg till steg" in edit_view.data
    assert b"Bas-URL" in edit_view.data

    response = client.post(
        f"/test/{case_id}/edit",
        data={
            "action": "update_side",
            "command[]": ["open", "unsupportedCommand"],
            "target[]": ["/", "css=.x"],
            "value[]": ["", ""],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Varning" in response.data

    note_response = client.post(
        f"/test/{case_id}/edit",
        data={"action": "add_note", "note_text": "nu startar vi"},
        follow_redirects=True,
    )
    assert note_response.status_code == 200

    data = json.loads(file_path.read_text(encoding="utf-8"))
    commands = data["tests"][0]["commands"]
    assert commands[-1]["command"] == "comment"
    assert commands[-1]["value"] == "nu startar vi"


def test_edit_meta_updates_base_url_and_validates_scheme():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    payload = build_side_payload()
    client.post(
        "/checks",
        data={
            "interval_minutes": "5",
            "side_file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "demo.side"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with selgrid_app.app.app_context():
        case = selgrid_app.TestCase.query.first()
        case_id = case.id
        file_path = Path(case.file_path)

    invalid_response = client.post(
        f"/test/{case_id}/edit",
        data={
            "action": "update_meta",
            "name": "Example.com",
            "interval_minutes": "5",
            "selenium_test_id": "t-1",
            "base_url": "example.com",
        },
        follow_redirects=True,
    )
    assert invalid_response.status_code == 200
    assert "Bas-URL måste börja med http:// eller https://".encode("utf-8") in invalid_response.data

    valid_response = client.post(
        f"/test/{case_id}/edit",
        data={
            "action": "update_meta",
            "name": "Example.com",
            "interval_minutes": "5",
            "selenium_test_id": "t-1",
            "base_url": "https://new.example.com",
            "active": "on",
        },
        follow_redirects=True,
    )
    assert valid_response.status_code == 200

    data = json.loads(file_path.read_text(encoding="utf-8"))
    assert data["urls"] == ["https://new.example.com"]


def test_wait_alias_supported_and_notimplemented_becomes_warning():
    calls = {"condition": None}

    class FakeWait:
        def __init__(self, driver, timeout):
            self.driver = driver
            self.timeout = timeout

        def until(self, condition):
            calls["condition"] = condition
            return True

    class FakeDriver:
        def find_elements(self, *_args):
            return []

    selgrid_app.WebDriverWait = FakeWait
    selgrid_app.perform_command(FakeDriver(), "waitForElement", "css=.item", "3")
    assert calls["condition"] is not None


def test_assert_title_uses_target_when_value_is_empty():
    class FakeDriver:
        title = "Example Domain"

    selgrid_app.perform_command(FakeDriver(), "assertTitle", "Example Domain", "")


def test_store_commands_save_values_to_variable_map():
    variables = {}

    class FakeElement:
        text = "Rubrik"

        def get_attribute(self, key):
            if key == "value":
                return "anna@example.com"
            return ""

    class FakeDriver:
        title = "Dashboard"

        def find_element(self, *_args):
            return FakeElement()

    driver = FakeDriver()
    selgrid_app.perform_command(driver, "storeText", "css=h1", "headerText", variables_map=variables)
    selgrid_app.perform_command(driver, "storeValue", "id=email", "emailValue", variables_map=variables)
    selgrid_app.perform_command(driver, "storeTitle", "pageTitle", "", variables_map=variables)

    assert variables == {
        "headerText": "Rubrik",
        "emailValue": "anna@example.com",
        "pageTitle": "Dashboard",
    }


def test_api_auth_required_exists_for_backward_compatibility():
    @selgrid_app.api_auth_required
    def sample():
        return "ok"

    assert sample() == "ok"


def test_serialize_test_case_failed_reports_zero_ms():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()

    with selgrid_app.app.app_context():
        user = selgrid_app.User(username="user1", password_hash="x")
        selgrid_app.db.session.add(user)
        selgrid_app.db.session.commit()

        case = selgrid_app.TestCase(
            owner_id=user.id,
            name="case",
            file_path="/tmp/demo.side",
            interval_minutes=5,
            selenium_test_id="t-1",
            active=True,
        )
        selgrid_app.db.session.add(case)
        selgrid_app.db.session.commit()

        run = selgrid_app.TestRun(
            test_case_id=case.id,
            started_at=selgrid_app.datetime.utcnow(),
            finished_at=selgrid_app.datetime.utcnow(),
            status="failed",
            total_duration_ms=1234,
            error_message="broken",
        )
        selgrid_app.db.session.add(run)
        selgrid_app.db.session.commit()

        payload = selgrid_app.serialize_test_case(case)
        assert payload["latest_run"]["status"] == "failed"
        assert payload["latest_run"]["duration_ms"] == 0


def test_invalid_side_file_gracefully_redirects_from_detail():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    payload = build_side_payload()
    client.post(
        "/checks",
        data={
            "interval_minutes": "5",
            "side_file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "demo.side"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with selgrid_app.app.app_context():
        case = selgrid_app.TestCase.query.first()
        case_id = case.id
        Path(case.file_path).write_text("{ not json", encoding="utf-8")

    response = client.get(f"/test/{case_id}", follow_redirects=True)
    assert response.status_code == 200
    assert "Kunde inte läsa .side-filen".encode("utf-8") in response.data


def test_test_detail_chart_labels_do_not_include_seconds():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    payload = build_side_payload()
    client.post(
        "/checks",
        data={
            "interval_minutes": "5",
            "side_file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "demo.side"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with selgrid_app.app.app_context():
        case = selgrid_app.TestCase.query.first()
        case_id = case.id
        selgrid_app.db.session.add(
            selgrid_app.TestRun(
                test_case_id=case.id,
                started_at=selgrid_app.datetime(2026, 2, 28, 11, 57, 49),
                finished_at=selgrid_app.datetime(2026, 2, 28, 11, 58, 10),
                status="success",
                total_duration_ms=21000,
            )
        )
        selgrid_app.db.session.commit()

    response = client.get(f"/test/{case_id}?trend_interval=7d")
    assert response.status_code == 200
    assert b'const labels = ["2026-02-28 12:57"]' in response.data




def test_test_detail_trend_defaults_to_last_24_hours_and_supports_intervals():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    payload = build_side_payload()
    client.post(
        "/checks",
        data={
            "interval_minutes": "5",
            "side_file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "demo.side"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    now = selgrid_app.datetime.utcnow()
    within_24h = now - selgrid_app.timedelta(hours=23)
    older_than_24h = now - selgrid_app.timedelta(hours=25)

    with selgrid_app.app.app_context():
        case = selgrid_app.TestCase.query.first()
        case_id = case.id
        selgrid_app.db.session.add_all([
            selgrid_app.TestRun(
                test_case_id=case.id,
                started_at=older_than_24h,
                finished_at=older_than_24h,
                status="success",
                total_duration_ms=1200,
            ),
            selgrid_app.TestRun(
                test_case_id=case.id,
                started_at=within_24h,
                finished_at=within_24h,
                status="success",
                total_duration_ms=800,
            ),
        ])
        selgrid_app.db.session.commit()

    default_response = client.get(f"/test/{case_id}")
    assert default_response.status_code == 200
    assert b'const durations = [800]' in default_response.data

    seven_day_response = client.get(f"/test/{case_id}?trend_interval=7d")
    assert seven_day_response.status_code == 200
    assert b'const durations = [1200, 800]' in seven_day_response.data

    all_time_response = client.get(f"/test/{case_id}?trend_interval=all")
    assert all_time_response.status_code == 200
    assert b'const durations = [1200, 800]' in all_time_response.data

    invalid_interval_response = client.get(f"/test/{case_id}?trend_interval=invalid")
    assert invalid_interval_response.status_code == 200
    assert b'const durations = [800]' in invalid_interval_response.data

def test_global_error_handler_returns_500_page():
    with selgrid_app.app.test_request_context("/"):
        response, status_code = selgrid_app.handle_unexpected_error(RuntimeError("boom"))
    assert status_code == 500
    assert "Något gick fel" in response


def test_dashboard_overview_and_checks_page_show_latest_status():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    payload = build_side_payload()
    client.post(
        "/checks",
        data={
            "interval_minutes": "5",
            "side_file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "demo.side"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with selgrid_app.app.app_context():
        case = selgrid_app.TestCase.query.first()
        selgrid_app.db.session.add(
            selgrid_app.TestRun(
                test_case_id=case.id,
                started_at=selgrid_app.datetime.utcnow(),
                finished_at=selgrid_app.datetime.utcnow(),
                status="failed",
                total_duration_ms=123,
            )
        )
        selgrid_app.db.session.commit()

    dashboard_response = client.get("/dashboard")
    assert dashboard_response.status_code == 200
    assert "Överblick av checkar".encode("utf-8") in dashboard_response.data
    assert b"Failed" in dashboard_response.data

    checks_response = client.get("/checks")
    assert checks_response.status_code == 200
    assert b"Hantera checkar" in checks_response.data
    assert b"Editera" in checks_response.data


def test_log_file_exists_in_project_root():
    assert selgrid_app.LOG_FILE_PATH.exists()

def test_admin_page_handles_users_and_tokens():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()

    with selgrid_app.app.app_context():
        selgrid_app.db.session.add(
            selgrid_app.User(
                username="admin",
                password_hash=selgrid_app.generate_password_hash("admin"),
            )
        )
        selgrid_app.db.session.commit()

    client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=True)

    create_user = client.post(
        "/admin",
        data={"action": "create_user", "username": "kalle", "password": "hemligt"},
        follow_redirects=True,
    )
    assert create_user.status_code == 200
    assert "Användare skapad".encode("utf-8") in create_user.data

    with selgrid_app.app.app_context():
        kalle = selgrid_app.User.query.filter_by(username="kalle").first()

    token_response = client.post(
        "/admin",
        data={"action": "create_token", "section": "tokens", "owner_id": str(kalle.id), "name": "api"},
        follow_redirects=True,
    )
    assert token_response.status_code == 200
    assert "Ny token".encode("utf-8") in token_response.data


def test_docu_and_status_pages_available_when_logged_in():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    docu_response = client.get("/docu")
    assert docu_response.status_code == 200
    assert b"Swagger UI" in docu_response.data

    openapi_response = client.get("/docu/openapi.json")
    assert openapi_response.status_code == 200
    assert openapi_response.is_json

    selgrid_app.check_service_health = lambda _url: {"ok": True, "url": _url, "details": "ok"}
    status_response = client.get("/status")
    assert status_response.status_code == 200
    assert b"Selenium Grid" in status_response.data


def test_api_results_returns_only_latest_run_summary():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()

    with selgrid_app.app.app_context():
        user = selgrid_app.User(
            username="api-user",
            password_hash=selgrid_app.generate_password_hash("secret"),
        )
        selgrid_app.db.session.add(user)
        selgrid_app.db.session.commit()

        case = selgrid_app.TestCase(
            owner_id=user.id,
            name="api check",
            file_path="/tmp/demo.side",
            interval_minutes=5,
            selenium_test_id="t-1",
            active=True,
        )
        selgrid_app.db.session.add(case)
        selgrid_app.db.session.commit()
        case_id = case.id

        older_run = selgrid_app.TestRun(
            test_case_id=case.id,
            started_at=selgrid_app.datetime.utcnow(),
            finished_at=selgrid_app.datetime.utcnow(),
            status="failed",
            total_duration_ms=100,
            error_message="boom",
        )
        selgrid_app.db.session.add(older_run)
        selgrid_app.db.session.commit()

        run = selgrid_app.TestRun(
            test_case_id=case.id,
            started_at=selgrid_app.datetime.utcnow(),
            finished_at=selgrid_app.datetime.utcnow(),
            status="success",
            total_duration_ms=321,
            error_message=None,
        )
        selgrid_app.db.session.add(run)
        selgrid_app.db.session.commit()
        latest_run_id = run.id

        token = create_api_token_for_user(user.id)

    client = selgrid_app.app.test_client()
    response = client.get(
        f"/api/tests/{case_id}/results",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["test_id"] == case_id
    assert payload["latest_result"]["id"] == latest_run_id
    assert payload["latest_result"]["status"] == "ok"
    assert payload["latest_result"]["total_duration_ms"] == 321
    assert payload["latest_result"]["timestamp"] is not None


def test_api_results_returns_404_for_missing_or_foreign_test():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()

    with selgrid_app.app.app_context():
        owner = selgrid_app.User(username="owner", password_hash="x")
        stranger = selgrid_app.User(username="stranger", password_hash="y")
        selgrid_app.db.session.add_all([owner, stranger])
        selgrid_app.db.session.commit()

        case = selgrid_app.TestCase(
            owner_id=owner.id,
            name="hidden",
            file_path="/tmp/demo.side",
            interval_minutes=5,
            selenium_test_id="t-1",
            active=True,
        )
        selgrid_app.db.session.add(case)
        selgrid_app.db.session.commit()
        case_id = case.id

        token = create_api_token_for_user(stranger.id, name="foreign")

    client = selgrid_app.app.test_client()
    response = client.get(
        f"/api/tests/{case_id}/results",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Test not found"



def test_api_database_maintenance_requires_admin_user():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()

    with selgrid_app.app.app_context():
        admin = selgrid_app.User(username="admin", password_hash="x")
        regular = selgrid_app.User(username="regular", password_hash="y")
        selgrid_app.db.session.add_all([admin, regular])
        selgrid_app.db.session.commit()

        token = create_api_token_for_user(regular.id, name="regular-token")

    client = selgrid_app.app.test_client()
    response = client.post(
        "/api/database/maintenance",
        json={"days_to_keep": 30},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Admin required"


def test_api_database_maintenance_rejects_invalid_days_to_keep():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()

    with selgrid_app.app.app_context():
        admin = selgrid_app.User(username="admin", password_hash="x")
        selgrid_app.db.session.add(admin)
        selgrid_app.db.session.commit()

        token = create_api_token_for_user(admin.id, name="admin-token")

    client = selgrid_app.app.test_client()
    response = client.post(
        "/api/database/maintenance",
        json={"days_to_keep": 0},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert "days_to_keep" in response.get_json()["error"]


def test_api_database_maintenance_removes_old_checkdata():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()

    with selgrid_app.app.app_context():
        admin = selgrid_app.User(
            username="admin",
            password_hash=selgrid_app.generate_password_hash("admin"),
        )
        selgrid_app.db.session.add(admin)
        selgrid_app.db.session.commit()

        case = selgrid_app.TestCase(
            owner_id=admin.id,
            name="cleanup-check",
            file_path="/tmp/demo.side",
            interval_minutes=5,
            selenium_test_id="t-1",
            active=True,
        )
        selgrid_app.db.session.add(case)
        selgrid_app.db.session.commit()

        old_run = selgrid_app.TestRun(
            test_case_id=case.id,
            started_at=selgrid_app.datetime.utcnow() - selgrid_app.timedelta(days=45),
            finished_at=selgrid_app.datetime.utcnow() - selgrid_app.timedelta(days=45),
            status="success",
            total_duration_ms=10,
        )
        recent_run = selgrid_app.TestRun(
            test_case_id=case.id,
            started_at=selgrid_app.datetime.utcnow() - selgrid_app.timedelta(days=5),
            finished_at=selgrid_app.datetime.utcnow() - selgrid_app.timedelta(days=5),
            status="success",
            total_duration_ms=10,
        )
        selgrid_app.db.session.add_all([old_run, recent_run])
        selgrid_app.db.session.commit()

        selgrid_app.db.session.add_all(
            [
                selgrid_app.StepMetric(
                    test_run_id=old_run.id,
                    step_index=1,
                    command="open",
                    target="/",
                    value="",
                    duration_ms=10,
                    status="success",
                ),
                selgrid_app.StepMetric(
                    test_run_id=recent_run.id,
                    step_index=1,
                    command="open",
                    target="/",
                    value="",
                    duration_ms=10,
                    status="success",
                ),
            ]
        )
        selgrid_app.db.session.commit()

        token = create_api_token_for_user(admin.id, name="admin-token")

    client = selgrid_app.app.test_client()
    response = client.post(
        "/api/database/maintenance",
        json={"days_to_keep": 30},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["message"] == "Databasunderhåll klart"
    assert payload["deleted_runs"] == 1
    assert payload["deleted_step_metrics"] == 1

    with selgrid_app.app.app_context():
        assert selgrid_app.TestRun.query.count() == 1
        assert selgrid_app.StepMetric.query.count() == 1


def test_help_page_contains_sections_and_commands():
    selgrid_app.app.config.update(TESTING=True)
    client = selgrid_app.app.test_client()

    response = client.get('/help')

    assert response.status_code == 200
    assert 'Hjälp'.encode('utf-8') in response.data
    assert 'Selenium-kommandon som stöds'.encode('utf-8') in response.data
    assert b'${USERNAME}' in response.data
    assert b'open | /login |' in response.data
    assert b'By.XPATH=//button' in response.data
    assert b'waitForElementClickable' in response.data


def test_resolve_locator_supports_python_by_syntax():
    by, selector = selgrid_app.resolve_locator("By.XPATH=//main//button")
    assert by == selgrid_app.By.XPATH
    assert selector == "//main//button"


def test_new_commands_clear_and_visibility_assertions():
    class FakeElement:
        def __init__(self, displayed=True):
            self.displayed = displayed
            self.cleared = False

        def clear(self):
            self.cleared = True

        def is_displayed(self):
            return self.displayed

    class FakeDriver:
        def __init__(self):
            self.elem = FakeElement(displayed=True)
            self.hidden_elem = FakeElement(displayed=False)

        def find_element(self, *_args):
            return self.elem

        def find_elements(self, *_args):
            return [self.hidden_elem]

    driver = FakeDriver()
    selgrid_app.perform_command(driver, "clear", "id=email", "")
    assert driver.elem.cleared is True

    selgrid_app.perform_command(driver, "assertElementVisible", "id=email", "")
    selgrid_app.perform_command(driver, "assertElementNotVisible", "css=.hidden", "")


def test_wait_for_element_clickable_uses_expected_condition():
    calls = {"condition": None}

    class FakeWait:
        def __init__(self, driver, timeout):
            self.driver = driver
            self.timeout = timeout

        def until(self, condition):
            calls["condition"] = condition
            return True

    selgrid_app.WebDriverWait = FakeWait
    selgrid_app.perform_command(object(), "waitForElementClickable", "By.XPATH=//button", "4")
    assert calls["condition"] is not None


def test_dashboard_shows_run_trend_bars():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    payload = build_side_payload()
    client.post(
        "/checks",
        data={
            "interval_minutes": "5",
            "side_file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "demo.side"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with selgrid_app.app.app_context():
        case = selgrid_app.TestCase.query.first()
        for idx, status in enumerate(["success", "failed", "success"]):
            selgrid_app.db.session.add(
                selgrid_app.TestRun(
                    test_case_id=case.id,
                    status=status,
                    error_message=None,
                    started_at=selgrid_app.datetime.utcnow(),
                    finished_at=selgrid_app.datetime.utcnow(),
                    total_duration_ms=1200 + (idx * 250),
                )
            )
        selgrid_app.db.session.commit()

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Senaste 3 körningar".encode("utf-8") in response.data
    assert b"run-trend-bar success" in response.data
    assert b"run-trend-bar failed" in response.data


def test_admin_manual_cleanup_removes_old_checkdata():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()

    with selgrid_app.app.app_context():
        admin = selgrid_app.User(
            username="admin",
            password_hash=selgrid_app.generate_password_hash("admin"),
        )
        selgrid_app.db.session.add(admin)
        selgrid_app.db.session.commit()

        case = selgrid_app.TestCase(
            owner_id=admin.id,
            name="cleanup-check",
            file_path="/tmp/demo.side",
            interval_minutes=5,
            selenium_test_id="t-1",
            active=True,
        )
        selgrid_app.db.session.add(case)
        selgrid_app.db.session.commit()

        old_run = selgrid_app.TestRun(
            test_case_id=case.id,
            started_at=selgrid_app.datetime.utcnow() - selgrid_app.timedelta(days=40),
            finished_at=selgrid_app.datetime.utcnow() - selgrid_app.timedelta(days=40),
            status="success",
            total_duration_ms=10,
        )
        recent_run = selgrid_app.TestRun(
            test_case_id=case.id,
            started_at=selgrid_app.datetime.utcnow() - selgrid_app.timedelta(days=5),
            finished_at=selgrid_app.datetime.utcnow() - selgrid_app.timedelta(days=5),
            status="success",
            total_duration_ms=10,
        )
        selgrid_app.db.session.add_all([old_run, recent_run])
        selgrid_app.db.session.commit()

        selgrid_app.db.session.add_all(
            [
                selgrid_app.StepMetric(
                    test_run_id=old_run.id,
                    step_index=1,
                    command="open",
                    target="/",
                    value="",
                    duration_ms=10,
                    status="success",
                ),
                selgrid_app.StepMetric(
                    test_run_id=recent_run.id,
                    step_index=1,
                    command="open",
                    target="/",
                    value="",
                    duration_ms=10,
                    status="success",
                ),
            ]
        )
        selgrid_app.db.session.commit()

    client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=True)

    response = client.post(
        "/admin",
        data={"action": "manual_cleanup", "days_to_keep": "30"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Databasunderhåll klart".encode("utf-8") in response.data

    with selgrid_app.app.app_context():
        assert selgrid_app.TestRun.query.count() == 1
        assert selgrid_app.StepMetric.query.count() == 1


def test_admin_can_update_retention_months():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()

    with selgrid_app.app.app_context():
        selgrid_app.db.session.add(
            selgrid_app.User(
                username="admin",
                password_hash=selgrid_app.generate_password_hash("admin"),
            )
        )
        selgrid_app.db.session.commit()

    client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=True)

    response = client.post(
        "/admin",
        data={"action": "update_retention_months", "months_to_keep": "9"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Schemalagd datarensning uppdaterad".encode("utf-8") in response.data

    with selgrid_app.app.app_context():
        setting = selgrid_app.DataRetentionSetting.query.first()
        assert setting is not None
        assert setting.months_to_keep == 9


def test_admin_page_shows_run_log_and_next_scheduled_run():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()

    with selgrid_app.app.app_context():
        admin = selgrid_app.User(
            username="admin",
            password_hash=selgrid_app.generate_password_hash("admin"),
        )
        owner = selgrid_app.User(
            username="owner",
            password_hash=selgrid_app.generate_password_hash("owner"),
        )
        selgrid_app.db.session.add(admin)
        selgrid_app.db.session.add(owner)
        selgrid_app.db.session.commit()

        case = selgrid_app.TestCase(
            owner_id=owner.id,
            name="Schema check",
            file_path="/tmp/demo.side",
            interval_minutes=15,
            selenium_test_id="t-1",
            active=True,
        )
        selgrid_app.db.session.add(case)
        selgrid_app.db.session.commit()

        selgrid_app.db.session.add(
            selgrid_app.TestRun(
                test_case_id=case.id,
                started_at=selgrid_app.datetime.utcnow(),
                finished_at=selgrid_app.datetime.utcnow(),
                status="success",
                total_duration_ms=321,
            )
        )
        selgrid_app.db.session.commit()

    job_id = "test-admin-page-run-log"
    selgrid_app.scheduler.add_job(
        lambda: None,
        trigger="date",
        run_date=selgrid_app.datetime.now(selgrid_app.timezone.utc) + selgrid_app.timedelta(minutes=30),
        id=job_id,
        replace_existing=True,
    )

    try:
        client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=True)
        response = client.get("/admin")
    finally:
        if selgrid_app.scheduler.get_job(job_id):
            selgrid_app.scheduler.remove_job(job_id)

    assert response.status_code == 200
    assert "Körlogg".encode("utf-8") in response.data
    assert "Nästa schemalagda körning".encode("utf-8") in response.data
    assert b"Schema check" in response.data
    assert b"owner" in response.data


def test_admin_runlog_can_be_filtered_by_check():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()

    with selgrid_app.app.app_context():
        admin = selgrid_app.User(
            username="admin",
            password_hash=selgrid_app.generate_password_hash("admin"),
        )
        owner = selgrid_app.User(
            username="owner",
            password_hash=selgrid_app.generate_password_hash("owner"),
        )
        selgrid_app.db.session.add_all([admin, owner])
        selgrid_app.db.session.commit()

        first_case = selgrid_app.TestCase(
            owner_id=owner.id,
            name="Check A",
            file_path="/tmp/a.side",
            interval_minutes=15,
            selenium_test_id="t-a",
            active=True,
        )
        second_case = selgrid_app.TestCase(
            owner_id=owner.id,
            name="Check B",
            file_path="/tmp/b.side",
            interval_minutes=15,
            selenium_test_id="t-b",
            active=True,
        )
        selgrid_app.db.session.add_all([first_case, second_case])
        selgrid_app.db.session.commit()

        selgrid_app.db.session.add_all(
            [
                selgrid_app.TestRun(
                    test_case_id=first_case.id,
                    started_at=selgrid_app.datetime.utcnow(),
                    finished_at=selgrid_app.datetime.utcnow(),
                    status="success",
                    total_duration_ms=100,
                ),
                selgrid_app.TestRun(
                    test_case_id=second_case.id,
                    started_at=selgrid_app.datetime.utcnow(),
                    finished_at=selgrid_app.datetime.utcnow(),
                    status="failed",
                    total_duration_ms=200,
                ),
            ]
        )
        selgrid_app.db.session.commit()

        first_case_id = first_case.id

    client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=True)
    response = client.get(f"/admin?section=runlog&runlog_test_case_id={first_case_id}")

    assert response.status_code == 200
    assert b"V\xc3\xa4lj check" in response.data
    assert b"Check A" in response.data
    assert b"Check B" in response.data
    assert b">Success<" in response.data
    assert b">Failed<" not in response.data


def test_admin_section_filter_shows_only_selected_panel():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()

    with selgrid_app.app.app_context():
        selgrid_app.db.session.add(
            selgrid_app.User(
                username="admin",
                password_hash=selgrid_app.generate_password_hash("admin"),
            )
        )
        selgrid_app.db.session.commit()

    client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=True)

    users_page = client.get("/admin?section=users")
    assert users_page.status_code == 200
    assert "Lägg till användare".encode("utf-8") in users_page.data
    assert "Nästa schemalagda körning".encode("utf-8") not in users_page.data


def test_admin_can_cancel_running_run():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()

    with selgrid_app.app.app_context():
        admin = selgrid_app.User(
            username="admin",
            password_hash=selgrid_app.generate_password_hash("admin"),
        )
        owner = selgrid_app.User(username="anna", password_hash="x")
        selgrid_app.db.session.add_all([admin, owner])
        selgrid_app.db.session.commit()

        case = selgrid_app.TestCase(
            owner_id=owner.id,
            name="case",
            file_path="/tmp/demo.side",
            interval_minutes=5,
            selenium_test_id="t-1",
            active=True,
        )
        selgrid_app.db.session.add(case)
        selgrid_app.db.session.commit()

        run = selgrid_app.TestRun(
            test_case_id=case.id,
            started_at=selgrid_app.datetime.utcnow(),
            status="running",
            total_duration_ms=0,
        )
        selgrid_app.db.session.add(run)
        selgrid_app.db.session.commit()
        run_id = run.id

    client.post("/login", data={"username": "admin", "password": "admin"}, follow_redirects=True)
    response = client.post(
        "/admin",
        data={"action": "cancel_run", "section": "runlog", "run_id": str(run_id)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Körning avbröts".encode("utf-8") in response.data

    with selgrid_app.app.app_context():
        updated = selgrid_app.TestRun.query.get(run_id)
        assert updated.status == "aborted"
        assert updated.error_message == "Avbruten av admin"
