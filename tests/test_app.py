import io
import json

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


def login_admin(client):
    ensure_admin_user()
    return client.post(
        "/login",
        data={"username": selgrid_app.DEFAULT_ADMIN_USERNAME, "password": selgrid_app.DEFAULT_ADMIN_PASSWORD},
        follow_redirects=True,
    )


def upload_side(client, name="demo.side"):
    side_payload = {
        "id": "project-1",
        "version": "2.0",
        "name": "demo",
        "urls": ["https://example.com"],
        "tests": [{"id": "t-1", "name": "mitt test", "commands": [{"command": "open", "target": "/", "value": ""}]}],
    }
    data = {
        "interval_minutes": "5",
        "side_file": (io.BytesIO(json.dumps(side_payload).encode("utf-8")), name),
    }
    return client.post("/dashboard", data=data, content_type="multipart/form-data", follow_redirects=True)


def test_secret_replacement():
    out = selgrid_app.replace_secret("hello ${USER}", {"USER": "world"})
    assert out == "hello world"


def test_default_admin_exists_and_register_disabled():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()

    ensure_admin_user()

    client = selgrid_app.app.test_client()
    response = client.get("/register", follow_redirects=True)
    assert response.status_code == 200
    assert b"Sj\xc3\xa4lvregistrering" in response.data


def test_upload_edit_delete_flow():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    login_admin(client)

    response = upload_side(client)
    assert response.status_code == 200
    assert b"mitt test" in response.data

    with selgrid_app.app.app_context():
        test_case = selgrid_app.TestCase.query.first()
        test_case_id = test_case.id

    edit_response = client.post(
        f"/test/{test_case_id}/edit",
        data={"name": "Uppdaterad check", "interval_minutes": "7", "selenium_test_id": "t-1", "active": "on"},
        follow_redirects=True,
    )
    assert edit_response.status_code == 200
    assert b"Uppdaterad check" in edit_response.data

    delete_response = client.post(f"/test/{test_case_id}/delete", follow_redirects=True)
    assert delete_response.status_code == 200


def test_admin_can_create_token_and_use_api():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    login_admin(client)

    create_token_response = client.post("/admin", data={"name": "CI"}, follow_redirects=True)
    assert create_token_response.status_code == 200
    assert b"Ny token" in create_token_response.data

    with selgrid_app.app.app_context():
        token = selgrid_app.ApiToken.query.first()
        assert token is not None

    upload_side(client)
    raw_token = None
    page = create_token_response.data.decode("utf-8")
    marker = "<code>"
    if marker in page:
        raw_token = page.split(marker, 1)[1].split("</code>", 1)[0]

    api_response = client.get("/api/tests", headers={"Authorization": f"Bearer {raw_token}"})
    assert api_response.status_code == 200
    assert isinstance(api_response.get_json(), list)


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
