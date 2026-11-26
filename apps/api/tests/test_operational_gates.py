import importlib.util

import pytest

from intelligence.config import ROOT, settings
from intelligence.db import connection
from intelligence.research import scores
from intelligence.vision_gateway import validate_boxes


def test_data_directory_cannot_escape_new_project(monkeypatch):
    monkeypatch.setattr(settings, "data_dir", "/home/ubuntu/minecraft")
    with pytest.raises(ValueError, match="inside this project"):
        _ = settings.storage


def test_legacy_database_target_is_rejected_before_connect(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql://ignored@127.0.0.1:5432/keftrade")
    with pytest.raises(ValueError, match="independent"):
        with connection():
            pass


def test_preflight_accounts_for_installation_and_temporary_space():
    spec = importlib.util.spec_from_file_location("intelligence_preflight", ROOT / "scripts/preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    gb = 1024**3
    assert module.check_budget(14 * gb, 2 * gb, 1 * gb)["passed"]
    assert not module.check_budget(14 * gb, 3 * gb, 2 * gb)["passed"]
    assert module.protected("/home/ubuntu/minecraft/world")


def test_calibration_bins_do_not_double_count_boundaries():
    import numpy as np

    truth = np.asarray([-0.01, 0.01, -0.01, 0.01])
    report = scores(truth, np.zeros(4), np.asarray([0.1, 0.2, 0.3, 0.9]))
    assert sum(b["count"] for b in report["calibration"]) == 4


def test_worker_output_cannot_escape_image_coordinates():
    assert validate_boxes([{"label": "Up", "confidence": 0.7, "xyxy": [0, 0, 640, 640]}])
    with pytest.raises(ValueError):
        validate_boxes([{"label": "Up", "confidence": 0.7, "xyxy": [-1, 0, 640, 640]}])
    with pytest.raises(ValueError):
        validate_boxes([{"label": "Up", "confidence": float("nan"), "xyxy": [0, 0, 640, 640]}])


def test_backup_restore_preserves_all_tables_and_immutable_guards(db, tmp_path):
    from intelligence.backup import create_and_verify
    from intelligence.storage import store_document
    from intelligence.util import now

    with connection() as conn:
        store_document(conn, "fixture", "https://example.invalid/backup", {"source": "test-only"}, now())
    receipt = create_and_verify(tmp_path / "isolated.kfi.zip")
    assert receipt["restore_verified"]
    assert receipt["row_counts"]["source_documents"] == 1
    assert receipt["off_host"] is False
