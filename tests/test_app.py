import io
import json

import app as selgrid_app


def reset_db():
    with selgrid_app.app.app_context():
        selgrid_app.db.drop_all()
        selgrid_app.db.create_all()


def register_and_login(client):
    client.post("/register", data={"username": "anna", "password": "hemligt"}, follow_redirects=True)


def test_secret_replacement():
    out = selgrid_app.replace_secret("hello ${USER}", {"USER": "world"})
    assert out == "hello world"


def test_upload_flow():
    selgrid_app.app.config.update(TESTING=True)
    reset_db()
    client = selgrid_app.app.test_client()
    register_and_login(client)

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
            }
        ],
    }

    data = {
        "interval_minutes": "5",
        "side_file": (io.BytesIO(json.dumps(side_payload).encode("utf-8")), "demo.side"),
    }
    response = client.post("/dashboard", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"mitt test" in response.data
