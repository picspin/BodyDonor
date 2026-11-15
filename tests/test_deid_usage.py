"""M1 测试：De-ID 模块、试用计数（幂等 + 超限拦截）、pipeline 落库。"""

from __future__ import annotations

import pytest
from tests.factories import make_dicom_dataset

from bodydonor import db
from bodydonor.deid import deid_dataset, inspect_dicom
from bodydonor.usage_counter import (
    TrialLimitError,
    check_trial_limits,
    current_periods,
    fingerprint_hash,
    record_case_usage,
)


class TestDeidModule:
    def test_inspect_collects_phi(self) -> None:
        """inspect_dicom：有值的患者字段进 removed_fields。"""
        ds = make_dicom_dataset(series_uid="1.2.3.4.5")
        result = inspect_dicom(ds)
        assert result.accepted is True
        assert result.removed_fields.get("patient_name") == "TEST^PATIENT"
        assert result.removed_fields.get("patient_id") == "PID-12345"
        assert result.removed_fields.get("accession_number") == "ACC-67890"

    def test_burned_yes_rejected(self) -> None:
        """BurnedInAnnotation == YES -> 拒收。"""
        ds = make_dicom_dataset(series_uid="1.2.3.4.5", burned_in="YES")
        result = inspect_dicom(ds)
        assert result.accepted is False
        assert result.burned_in_annotation == "YES"

    def test_burned_missing_unknown_with_warning(self) -> None:
        """BurnedInAnnotation 缺失 -> UNKNOWN + 警告。"""
        ds = make_dicom_dataset(series_uid="1.2.3.4.5")
        del ds.BurnedInAnnotation
        result = inspect_dicom(ds)
        assert result.accepted is True
        assert result.burned_in_annotation == "UNKNOWN"
        assert any("BurnedInAnnotation" in w for w in result.warnings)

    def test_deid_dataset_strips_fields_inplace(self) -> None:
        """deid_dataset：原地剥离患者字段。"""
        ds = make_dicom_dataset(series_uid="1.2.3.4.5")
        result = deid_dataset(ds)
        assert result.accepted is True
        assert str(ds.PatientName or "") == ""
        assert str(ds.PatientID or "") == ""
        assert str(ds.AccessionNumber or "") == ""

    def test_deid_no_rewrite_on_reject(self) -> None:
        """拒收时不改写（原样返回）。"""
        ds = make_dicom_dataset(series_uid="1.2.3.4.5", burned_in="YES")
        result = deid_dataset(ds)
        assert result.accepted is False
        assert str(ds.PatientName) == "TEST^PATIENT"  # 未被剥离


@pytest.fixture
def fresh_db(tmp_path):  # noqa: ANN001
    """全新临时库。"""
    url = f"sqlite:///{tmp_path}/t.db"
    db.init_db(url)
    engine = db._engine(url)
    yield url, engine
    engine.dispose()


class TestUsageCounter:
    def test_record_usage_counts_once_per_case(self, fresh_db) -> None:
        """计数写路径：一个 Case 计 1 例；重复导入同一 Case 不重复计（幂等）。"""
        url, engine = fresh_db
        fp = fingerprint_hash()
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            u1 = record_case_usage(s, "case-1", fp)
            assert u1["recorded"] is True
            assert u1["yearly_used"] == 1
            u2 = record_case_usage(s, "case-1", fp)  # 重复
            assert u2["recorded"] is False
            assert u2["yearly_used"] == 1  # 不重复计
            u3 = record_case_usage(s, "case-2", fp)
            assert u3["yearly_used"] == 2
            s.commit()
            s.close()

    def test_yearly_and_monthly_periods(self, fresh_db) -> None:
        """双行流水：年 + 月两个 period。"""
        url, engine = fresh_db
        fp = fingerprint_hash()
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy import select
            from sqlalchemy.orm import Session

            from bodydonor.models import UsageLedger

            s = Session(conn)
            record_case_usage(s, "case-1", fp)
            periods = {
                r[0]
                for r in s.execute(
                    select(UsageLedger.period).where(UsageLedger.case_id == "case-1")
                ).all()
            }
            yearly, monthly = current_periods()
            assert periods == {yearly, monthly}
            s.commit()
            s.close()

    def test_monthly_limit_blocks(self, fresh_db, monkeypatch) -> None:  # noqa: ANN001
        """超限拦截：月度 20 例达上限后第 21 例 raise TrialLimitError。"""
        url, engine = fresh_db
        fp = fingerprint_hash()
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            for i in range(20):
                record_case_usage(s, f"case-{i}", fp)
            with pytest.raises(TrialLimitError) as exc_info:
                check_trial_limits(s, fp)
            assert "月" in str(exc_info.value)
            s.commit()
            s.close()

    def test_yearly_limit_blocks(self, fresh_db) -> None:  # noqa: ANN001
        """超限拦截：年度 200 例达上限（月度未超时年度先拦）。"""
        url, engine = fresh_db
        fp = fingerprint_hash()
        yearly, _monthly = current_periods()
        # 直接在流水里造 200 条年度记录（period='2026' 年度桶；当月桶不动）
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy.orm import Session

            from bodydonor.models import UsageLedger

            s = Session(conn)
            for i in range(200):
                s.add(
                    UsageLedger(
                        fingerprint_hash=fp,
                        license_id=None,
                        period=yearly,
                        case_id=f"old-{i}",
                    )
                )
            s.commit()
            with pytest.raises(TrialLimitError) as exc_info:
                check_trial_limits(s, fp)
            assert yearly in str(exc_info.value)
            s.close()

    def test_license_exempts_limits(self, fresh_db) -> None:
        """有 license_id 时不受试用限制。"""
        url, engine = fresh_db
        fp = fingerprint_hash()
        with db.write_lock, engine.connect() as conn:
            from sqlalchemy import select
            from sqlalchemy.orm import Session

            from bodydonor.models import LicenseState

            s = Session(conn)
            # 先触发一次预检，让 LicenseState 行被创建（get_or_create）
            check_trial_limits(s, fp)
            state = s.execute(select(LicenseState)).scalars().first()
            state.license_id = "LIC-123"
            s.commit()
            result = check_trial_limits(s, fp)
            assert result["enforced"] is False
            assert result["reason"] == "licensed"
            s.close()


class TestPipelineCommit:
    def test_scan_then_commit_persists(self, tmp_path) -> None:  # noqa: ANN001
        """scan -> commit：Case/Series 落库 + 像素落盘 + 计数。"""
        from tests.factories import make_nifti

        from bodydonor.config import Settings
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        data_dir = tmp_path / "data"
        settings = Settings(data_dir=data_dir)
        settings.database.url = f"sqlite:///{tmp_path}/t.db"
        db.init_db(settings.database.url)
        storage = LocalFsStorage(data_dir / "storage")

        nii = make_nifti(tmp_path / "img.nii.gz")
        outcome = scan(nii, settings, modality="CT")
        assert outcome.report.n_series == 1
        committed = commit(outcome, settings, storage)
        assert len(committed.case_ids) == 1
        assert committed.usage.get("recorded") is True

        # 落库验证
        engine = db._engine(settings.database.url)
        with engine.connect() as conn:
            from sqlalchemy import select
            from sqlalchemy.orm import Session

            from bodydonor.models import Case, Series

            s = Session(conn)
            cases = s.execute(select(Case)).scalars().all()
            series = s.execute(select(Series)).scalars().all()
            assert len(cases) == 1
            assert len(series) == 1
            assert series[0].storage_key is not None
            s.close()
        engine.dispose()

    def test_commit_idempotent_on_reimport(self, tmp_path) -> None:  # noqa: ANN001
        """重复导入同一 Series：不重复计、不重复建 Case/Series。"""
        from tests.factories import make_nifti

        from bodydonor.config import Settings
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        data_dir = tmp_path / "data"
        settings = Settings(data_dir=data_dir)
        settings.database.url = f"sqlite:///{tmp_path}/t.db"
        db.init_db(settings.database.url)
        storage = LocalFsStorage(data_dir / "storage")

        nii = make_nifti(tmp_path / "img.nii.gz")
        o1 = commit(scan(nii, settings, modality="CT"), settings, storage)
        o2 = commit(scan(nii, settings, modality="CT"), settings, storage)
        assert o1.usage["yearly_used"] == 1
        assert o2.usage["recorded"] is False
        assert o2.usage["yearly_used"] == 1

    def test_commit_respects_user_overrides(self, tmp_path) -> None:  # noqa: ANN001
        """用户勾选：只落被勾选的 Series。"""
        from tests.factories import make_dicom_folder

        from bodydonor.config import Settings
        from bodydonor.pipeline import commit, scan
        from bodydonor.storage import LocalFsStorage

        data_dir = tmp_path / "data"
        settings = Settings(data_dir=data_dir)
        settings.database.url = f"sqlite:///{tmp_path}/t.db"
        db.init_db(settings.database.url)
        storage = LocalFsStorage(data_dir / "storage")

        root = make_dicom_folder(tmp_path / "dicom", n_series=2, slices_per_series=40)
        outcome = scan(root, settings)
        assert len(outcome.report.series) == 2
        chosen = outcome.report.series[0].series_id
        committed = commit(outcome, settings, storage, user_overrides=[chosen])
        assert len(committed.case_ids) == 1
        assert committed.selected_series_ids == [chosen]
