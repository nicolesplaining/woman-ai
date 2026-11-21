"""API behaviour: jobs come back after a restart, and print files need a volunteer's sign-off."""
import pytest
from fastapi.testclient import TestClient

from app import main, pipeline


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    pipeline.JOBS.clear()
    yield TestClient(main.app)
    pipeline.JOBS.clear()


@pytest.fixture
def finished_job(client):
    job = pipeline.Job("photo")
    pipeline.JOBS[job.id] = job
    for name in ("kit.3mf", "fit_ring.3mf", "palm.stl", "fitted_package.zip", "view_arm.stl", "overlay_top.jpg", "build_guide.md"):
        (job.dir / name).write_bytes(b"x" * 100)
    job.status = "done"
    job.save()
    return job


@pytest.mark.parametrize("name", ["kit.3mf", "fit_ring.3mf", "palm.stl", "fitted_package.zip"])
def test_print_files_are_locked_until_sign_off(client, finished_job, name):
    r = client.get(f"/api/jobs/{finished_job.id}/files/{name}")
    assert r.status_code == 403


@pytest.mark.parametrize("name", ["view_arm.stl", "overlay_top.jpg", "build_guide.md"])
def test_preview_files_are_open_before_sign_off(client, finished_job, name):
    r = client.get(f"/api/jobs/{finished_job.id}/files/{name}")
    assert r.status_code == 200


def test_print_files_unlock_after_sign_off(client, finished_job):
    r = client.post(f"/api/jobs/{finished_job.id}/approve", json={"name": "Danica Sun"})
    assert r.status_code == 200

    r = client.get(f"/api/jobs/{finished_job.id}/files/kit.3mf")
    assert r.status_code == 200


def test_job_is_served_after_restart(client, finished_job):
    pipeline.JOBS.clear()

    r = client.get(f"/api/jobs/{finished_job.id}")

    assert r.status_code == 200
    assert r.json()["status"] == "done"
