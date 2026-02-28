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


def test_secret_replacement():
    out = selgrid_app.replace_secret("hello ${USER}", {"USER": "world"})
    assert out == "hello world"


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
        data={"action": "create_token", "owner_id": str(kalle.id), "name": "api"},
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
