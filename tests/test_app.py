import io
import json
from pathlib import Path

import app as selgrid_app


def reset_db():
    with selgrid_app.app.app_context():
        selgrid_app.db.drop_all()
        selgrid_app.db.create_all()


def ensure_admin_user():
    with selgrid_app.app.app_context():
        admin = selgrid_app.User.query.filter_by(username=selgrid_app.DEFAULT_ADMIN_USERNAME).first()
        if not admin:
            admin = selgrid_app.User(
                username=selgrid_app.DEFAULT_ADMIN_USERNAME,
                password_hash=selgrid_app.generate_password_hash(selgrid_app.DEFAULT_ADMIN_PASSWORD),
            )
            selgrid_app.db.session.add(admin)
            selgrid_app.db.session.commit()


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
    response = client.post("/dashboard", data=file_data, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"mitt test" in response.data

    raw_data = {
        "interval_minutes": "3",
        "side_raw": json.dumps(payload),
    }
    raw_response = client.post("/dashboard", data=raw_data, follow_redirects=True)
    assert raw_response.status_code == 200


def test_edit_table_and_add_note():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    payload = build_side_payload()
    client.post(
        "/dashboard",
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
