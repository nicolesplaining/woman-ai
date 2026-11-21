"""Jobs are saved to disk, so a server restart doesn't lose finished results."""
import pytest

from app import pipeline


@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    pipeline.JOBS.clear()
    yield tmp_path
    pipeline.JOBS.clear()


def _restart():
    pipeline.JOBS.clear()


def test_finished_job_survives_restart(jobs_dir):
    job = pipeline.Job("photo")
    pipeline.JOBS[job.id] = job
    with job.step("inputs"):
        pass
    job.status = "done"
    job.result = {"measurements": {"wrist_width": 45.1}}
    job.save()
    before = job.view()

    _restart()
    loaded = pipeline.get_job(job.id)

    assert loaded is not None
    assert loaded.view() == before


def test_job_running_at_restart_is_reported_as_interrupted(jobs_dir):
    job = pipeline.Job("photo")
    pipeline.JOBS[job.id] = job
    job.status = "running"
    job.save()

    _restart()
    loaded = pipeline.get_job(job.id)

    assert loaded.status == "error"
    assert "restart" in loaded.error["message"].lower()


def test_unknown_or_malformed_job_id_returns_none(jobs_dir):
    assert pipeline.get_job("0123456789ab") is None
    assert pipeline.get_job("../../etc") is None


def test_approval_is_saved(jobs_dir):
    job = pipeline.Job("photo")
    pipeline.JOBS[job.id] = job
    job.status = "done"
    job.approve("Nicole Ma")

    _restart()

    assert pipeline.get_job(job.id).approved["name"] == "Nicole Ma"
