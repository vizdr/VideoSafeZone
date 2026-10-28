"""The fake AWS a Lambda handler runs against (PYTEST.md, Phase 2)."""
import pytest

from fakes import FakeBoto3, FakeTable


@pytest.fixture
def aws(monkeypatch):
    """aws(lambda_module, cameras=[...], clips=[...], page_size=None) -> FakeBoto3.

    Replaces the module's `boto3` and its module-level `cameras_table`. Queue client answers with
    fake.stub("s3").add_response(...); a queued answer left unused fails the test afterwards, so
    a call the handler should have made and didn't is caught too.
    """
    made = []

    def install(lam, cameras=(), clips=(), page_size=None):
        fake = FakeBoto3(cameras=FakeTable(cameras, page_size=page_size),
                         clips=FakeTable(clips, range_key="startTs", page_size=page_size))
        monkeypatch.setattr(lam, "boto3", fake)
        if hasattr(lam, "cameras_table"):
            monkeypatch.setattr(lam, "cameras_table", fake.tables["cameras"])
        made.append(fake)
        return fake

    yield install
    for fake in made:
        fake.verify()
