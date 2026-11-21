"""End to end on the synthetic sample: the full job runs, with and without Claude's render review."""
import pytest

from app import ai, pipeline


@pytest.fixture
def run_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    pipeline.JOBS.clear()

    def run():
        d = pipeline.sample_inputs("partial_hand")
        job = pipeline.Job("photo")
        pipeline.run(job, pipeline.Inputs(mode="photo", photos=d["photos"]))
        return job

    yield run
    pipeline.JOBS.clear()


def _step(job, key):
    return next(s for s in job.steps if s["key"] == key)


def test_review_is_skipped_cleanly_without_claude(run_sample):
    job = run_sample()

    assert job.status == "done", job.error
    assert _step(job, "review")["status"] == "warn"
    assert job.result["review"]["history"] == []
    assert (job.dir / "kit.3mf").is_file()


def test_review_adjustments_are_applied_and_rebuilt(run_sample, monkeypatch):
    verdicts = [
        {"looks_good": False, "issues": ["Tight at the wrist"], "adjustments": {"gap_mm": 5.0}, "summary": "More padding."},
        {"looks_good": True, "issues": [], "adjustments": {}, "summary": "Fits well."},
    ]
    seen_images = []

    def fake_review(images, settings, bounds, context):
        seen_images.append(len(images))
        return verdicts.pop(0)

    monkeypatch.setattr(ai, "review_fit", fake_review)
    job = run_sample()

    assert job.status == "done", job.error
    review = job.result["review"]
    assert [h["pass"] for h in review["history"]] == [1, 2]
    assert review["final"]["gap_mm"] == 5.0
    assert job.result["gauntlet"]["gap_mm"] == 5.0
    assert seen_images == [3, 3]
