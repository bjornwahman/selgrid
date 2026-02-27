import io
import json

import app as selgrid_app


def reset_db():
    with selgrid_app.app.app_context():
        selgrid_app.db.drop_all()
        selgrid_app.db.create_all()


def register_and_login(client):
    client.post("/register", data={"username": "anna", "password": "hemligt"}, follow_redirects=True)


def upload_side(client, name="demo.side"):
    side_payload = {
        "id": "project-1",
        "version": "2.0",
        "name": "demo",
        "urls": ["https://example.com"],
        "tests": [
            {
                "id": "t-1",
                "name": "mitt test",
                "commands": [{"command": "open", "target": "/", "value": ""}],
            },
            {
                "id": "t-2",
                "name": "andra test",
                "commands": [{"command": "open", "target": "/about", "value": ""}],
            },
        ],
    }

    data = {
        "interval_minutes": "5",
        "side_file": (io.BytesIO(json.dumps(side_payload).encode("utf-8")), name),
    }
    return client.post("/dashboard", data=data, content_type="multipart/form-data", follow_redirects=True)


def test_secret_replacement():
    out = selgrid_app.replace_secret("hello ${USER}", {"USER": "world"})
    assert out == "hello world"


def test_upload_flow():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

    response = upload_side(client)
    assert response.status_code == 200
    assert b"mitt test" in response.data


def test_edit_and_delete_check():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)
    upload_side(client)

    with selgrid_app.app.app_context():
        test_case = selgrid_app.TestCase.query.first()
        test_case_id = test_case.id

    edit_response = client.post(
        f"/test/{test_case_id}/edit",
        data={
            "name": "Uppdaterat namn",
            "interval_minutes": "7",
            "selenium_test_id": "t-2",
            "active": "on",
        },
        follow_redirects=True,
    )
    assert edit_response.status_code == 200
    assert b"Uppdaterat namn" in edit_response.data

    with selgrid_app.app.app_context():
        updated = selgrid_app.TestCase.query.get(test_case_id)
        assert updated.interval_minutes == 7
        assert updated.selenium_test_id == "t-2"

    delete_response = client.post(f"/test/{test_case_id}/delete", follow_redirects=True)
    assert delete_response.status_code == 200

    with selgrid_app.app.app_context():
        assert selgrid_app.TestCase.query.get(test_case_id) is None


def test_wait_for_element_alias_uses_visibility_wait(monkeypatch):
    calls = {"condition": None}

    class FakeWait:
        def __init__(self, driver, timeout):
            self.driver = driver
            self.timeout = timeout

        def until(self, condition):
            calls["condition"] = condition
            return True

    monkeypatch.setattr(selgrid_app, "WebDriverWait", FakeWait)

    class FakeDriver:
        pass

    selgrid_app.perform_command(FakeDriver(), "waitForElement", "css=.item", "3")
    assert calls["condition"] is not None
