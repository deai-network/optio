"""OptioEngineService.materialize_upload — clamator RPC handler.

Reads the GridFS-staged blob by id, hands the bytes to Optio.materialize_upload,
and returns {ok, path} / {ok:false, reason}. Bytes are not in the params, only
the blobId.
"""

from unittest.mock import AsyncMock, MagicMock

from bson import ObjectId

from optio_core._generated.optio_engine import MaterializeUploadParams
from optio_core._engine_service import OptioEngineService


async def test_materialize_upload_reads_blob_and_writes():
    optio = MagicMock()
    optio.read_blob_bytes = AsyncMock(return_value=b"hello")
    optio.materialize_upload = AsyncMock(return_value="uploads/n.md")
    svc = OptioEngineService(optio)

    blob_id = str(ObjectId())
    res = await svc.materialize_upload(
        MaterializeUploadParams.model_validate(
            {"processId": "p1", "blobId": blob_id, "filename": "n.md"}
        )
    )

    assert res.root.ok is True
    assert res.root.path == "uploads/n.md"
    optio.read_blob_bytes.assert_awaited_once_with(ObjectId(blob_id))
    optio.materialize_upload.assert_awaited_once_with("p1", b"hello", "n.md")


async def test_materialize_upload_failure_returns_reason():
    optio = MagicMock()
    optio.read_blob_bytes = AsyncMock(return_value=b"hello")
    optio.materialize_upload = AsyncMock(side_effect=RuntimeError("no upload writer"))
    svc = OptioEngineService(optio)

    res = await svc.materialize_upload(
        MaterializeUploadParams.model_validate(
            {"processId": "p1", "blobId": str(ObjectId()), "filename": "n.md"}
        )
    )

    assert res.root.ok is False
    assert "no upload writer" in res.root.reason


async def test_materialize_upload_missing_blob_returns_reason():
    optio = MagicMock()
    optio.read_blob_bytes = AsyncMock(side_effect=RuntimeError("gridfs no file"))
    optio.materialize_upload = AsyncMock(return_value="uploads/n.md")
    svc = OptioEngineService(optio)

    res = await svc.materialize_upload(
        MaterializeUploadParams.model_validate(
            {"processId": "p1", "blobId": str(ObjectId()), "filename": "n.md"}
        )
    )

    assert res.root.ok is False
    assert "gridfs no file" in res.root.reason
    optio.materialize_upload.assert_not_awaited()
