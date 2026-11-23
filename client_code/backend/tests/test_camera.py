"""임시 SQLite의 무결성과 카메라 DTO 검증. 운영 DB·장치에 접근하지 않는다."""

from datetime import datetime, timezone
import unittest

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from client_code.backend.models.camera import Camera
from client_code.backend.schemas.camera import (
    CameraCreate, CameraUpdate, CameraSummary, CameraResponse, CameraListResponse,
)


class CameraSchemaTests(unittest.TestCase):
    def test_create_defaults_and_trim(self):
        camera = CameraCreate(name="  정문  ", camera_type="webcam", webcam_index=0)
        self.assertEqual(camera.name, "정문")
        self.assertTrue(camera.is_enabled)
        self.assertIsNone(camera.rtsp_url)
        url = "rtsp://CAMERA.example:554/live?channel=1"
        edge = CameraCreate(name="후문", camera_type="edge_node", rtsp_url=f" {url} ")
        self.assertEqual(edge.rtsp_url, url)
        CameraCreate(name="가" * 100, camera_type="webcam", webcam_index=0)

    def test_invalid_create(self):
        base = dict(name="정문", camera_type="webcam", webcam_index=0)
        for patch in (
            {"name": " \t "}, {"name": "가" * 101}, {"name": 1},
            {"webcam_index": -1}, {"webcam_index": True}, {"webcam_index": 1.0},
            {"webcam_index": "0"}, {"webcam_index": None},
            {"is_enabled": 1}, {"is_enabled": "true"}, {"is_enabled": None},
            {"camera_type": "other"}, {"camera_type": "edge_node"},
            {"rtsp_url": "rtsp://host/live"}, {"edge_api_base_url": "http://host"},
            {"camera_id": 1}, {"unknown": 1},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                CameraCreate(**(base | patch))

    def test_url_rules(self):
        for url in ("", "http://host/live", "rtsp:///live", "rtsp://user:pass@host/live",
                    "rtsp://host:0/live", "rtsp://host:65536/live", "rtsp://host:/live",
                    "rtsp://host:abc/live", "rtsp://host/live#", "rtsp://ho st/live",
                    "rtsp://host/li\nve", "rtsp://host/\\live"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                CameraCreate(name="후문", camera_type="edge_node", rtsp_url=url)
        for url in ("rtsps://[::1]:8554/live", "rtsp://host/live?channel=1"):
            CameraCreate(name="후문", camera_type="edge_node", rtsp_url=url)
        for url in ("http://host/api?", "http://host/api?token=x", "rtsp://host", "https://user@host"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                CameraUpdate(edge_api_base_url=url)
        CameraUpdate(edge_api_base_url="https://host:443/api")

    def test_patch_presence_and_merged_validation(self):
        patch = CameraUpdate(edge_api_base_url=None)
        self.assertEqual(patch.model_dump(exclude_unset=True), {"edge_api_base_url": None})
        self.assertEqual(CameraUpdate(is_enabled=False).model_dump(exclude_unset=True), {"is_enabled": False})
        for data in ({}, {"name": None}, {"is_enabled": None}, {"camera_type": "webcam"},
                     {"camera_id": 1}, {"created_at": "now"}, {"updated_at": "now"}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                CameraUpdate(**data)
        original = CameraCreate(name="정문", camera_type="webcam", webcam_index=0)
        update = CameraUpdate(webcam_index=None)
        with self.assertRaises(ValidationError):
            CameraCreate.model_validate(original.model_dump() | update.model_dump(exclude_unset=True))

    def test_error_text_and_repr_hide_input(self):
        secret_url = "rtsp://user:example-secret@host/live"
        with self.assertRaises(ValidationError) as caught:
            CameraCreate(name="후문", camera_type="edge_node", rtsp_url=secret_url)
        self.assertNotIn(secret_url, str(caught.exception))
        self.assertNotIn("rtsp://host", repr(CameraUpdate(rtsp_url="rtsp://host")))


class CameraModelTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Camera.__table__.create(self.engine)

    def test_roundtrip_defaults_responses_and_update(self):
        with Session(self.engine) as db:
            camera = Camera(name="정문", camera_type="webcam", webcam_index=0)
            db.add(camera)
            db.commit()
            db.refresh(camera)
            self.assertEqual(camera.created_at, camera.updated_at)
            self.assertEqual(camera.created_at.tzinfo, timezone.utc)
            original_time = camera.updated_at
            camera.name = "정문"
            db.commit()
            self.assertEqual(camera.updated_at, original_time)
            camera.name = "출입구"
            db.commit()
            self.assertGreaterEqual(camera.updated_at, original_time)
            summary = CameraSummary.model_validate(camera)
            detail = CameraResponse.model_validate(camera)
            self.assertNotIn("rtsp_url", summary.model_dump())
            self.assertIn("rtsp_url", detail.model_dump())
            self.assertTrue(summary.model_dump(mode="json")["created_at"].endswith("Z"))
            self.assertEqual(CameraListResponse(items=[detail]).model_dump()["items"][0].keys(), summary.model_dump().keys())
            self.assertEqual(CameraListResponse(items=[]).model_dump(), {"items": []})

    def insert_raw(self, **overrides):
        values = dict(name="정문", camera_type="webcam", webcam_index=0, rtsp_url=None,
                      edge_api_base_url=None, is_enabled=1)
        values.update(overrides)
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO cameras (name, camera_type, webcam_index, rtsp_url,
                    edge_api_base_url, is_enabled, created_at, updated_at)
                VALUES (:name, :camera_type, :webcam_index, :rtsp_url,
                    :edge_api_base_url, :is_enabled, '2026-10-07T00:00:00+00:00',
                    '2026-10-07T00:00:00+00:00')
            """), values)

    def test_db_constraints_bypass_dto(self):
        for patch in ({"name": ""}, {"name": " "}, {"name": "가" * 101},
                      {"camera_type": "bad"}, {"webcam_index": -1}, {"webcam_index": 0.5},
                      {"webcam_index": None}, {"is_enabled": 2},
                      {"rtsp_url": "rtsp://host"}, {"edge_api_base_url": "http://host"},
                      {"camera_type": "edge_node", "webcam_index": None},
                      {"camera_type": "edge_node", "webcam_index": None, "rtsp_url": ""}):
            with self.subTest(patch=patch), self.assertRaises(IntegrityError):
                self.insert_raw(**patch)

    def test_unique_includes_disabled_and_nulls_allowed(self):
        self.insert_raw(is_enabled=0)
        with self.assertRaises(IntegrityError):
            self.insert_raw()
        self.insert_raw(webcam_index=1)  # 두 웹캠 모두 rtsp_url=NULL 가능
        edge = dict(camera_type="edge_node", webcam_index=None, rtsp_url="rtsp://host/1")
        self.insert_raw(**edge, is_enabled=0)
        with self.assertRaises(IntegrityError):
            self.insert_raw(**edge)
        self.insert_raw(**(edge | {"rtsp_url": "rtsp://host/2"}))

    def test_response_requires_aware_time(self):
        with self.assertRaises(ValidationError):
            CameraSummary(camera_id=1, name="정문", camera_type="webcam", is_enabled=True,
                          created_at=datetime(2026, 10, 7), updated_at=datetime(2026, 10, 7))


if __name__ == "__main__":
    unittest.main()
