"""임시 DB만 사용하는 인증 계약·실패·동시성·실제 HTTP 검증."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier, Thread
import time
import unittest
from unittest.mock import patch

from fastapi import Depends
from fastapi.testclient import TestClient
import httpx
import jwt
import uvicorn

from client_code.backend.database import DEFAULT_DB_PATH
from client_code.backend.dependencies import require_user_id
from client_code.backend.errors import APIError
from client_code.backend.main import create_app
from client_code.backend.security import ALGORITHM


PREFIX = "/api/v1/auth"
SIGNUP = {"username": "admin", "password": "test-pass", "password_confirm": "test-pass"}
LOGIN = {"username": "admin", "password": "test-pass"}


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "auth.db"
        self.app = create_app(self.db_path)

        # 명세 밖 엔드포인트는 테스트 앱에만 등록, 제품 앱에는 없음.
        @self.app.get("/_test/protected")
        def protected(user_id: int = Depends(require_user_id)):
            return {"user_id": user_id}

        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.service = self.app.state.auth_service

    def signup(self):
        response = self.client.post(PREFIX + "/signup", json=SIGNUP)
        self.assertEqual(response.status_code, 201)
        return response.json()

    def login(self):
        response = self.client.post(PREFIX + "/login", json=LOGIN)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def protected(self, token):
        return self.client.get("/_test/protected", headers={"Authorization": "Bearer " + token})

    def test_signup_status_before_and_after_signup(self):
        response = self.client.get(PREFIX + "/signup-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"signup_required": True})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.signup()
        response = self.client.get(PREFIX + "/signup-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"signup_required": False})
        self.assertEqual(response.headers["cache-control"], "no-store")
        # 먼저 받은 true는 가입 권한 예약이 아니며 서버는 가입 시 다시 검증한다.
        self.assertEqual(self.client.post(PREFIX + "/signup", json=SIGNUP).status_code, 409)

    def test_signup_status_database_failure_is_not_signup_required(self):
        with patch.object(self.service.repository, "exists", side_effect=RuntimeError("private DB details")):
            response = self.client.get(PREFIX + "/signup-status")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "INTERNAL_SERVER_ERROR")
        self.assertNotIn("signup_required", response.json())
        self.assertNotIn("private DB details", response.text)

    def test_signup_storage_and_closed_signup(self):
        self.assertEqual(self.signup(), {"user_id": 1, "username": "admin"})
        response = self.client.post(PREFIX + "/signup", json={**SIGNUP, "username": "other"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "SIGNUP_CLOSED")
        with closing(sqlite3.connect(self.db_path)) as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(names, {"users", "server_runs", "cameras", "global_person_manage", "events", "sqlite_sequence"})
            hashed, timestamp = db.execute("SELECT password_hash, created_at FROM users").fetchone()
            self.assertTrue(hashed.startswith("$argon2id$"))
            self.assertNotIn(SIGNUP["password"], hashed)
            self.assertTrue(timestamp.endswith("+00:00"))

    def test_input_boundaries_and_mismatch(self):
        for field in ("username", "password"):
            for value in ("abc", None, 1234):
                response = self.client.post(PREFIX + "/signup", json={**SIGNUP, field: value})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("test-pass", response.text)
        mismatch = self.client.post(PREFIX + "/signup", json={**SIGNUP, "password_confirm": "different"})
        self.assertEqual(mismatch.json()["error"]["code"], "PASSWORD_CONFIRM_MISMATCH")
        response = self.client.post(PREFIX + "/signup", json={"username": "가나다라", "password": " ab ", "password_confirm": " ab "})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.post(PREFIX + "/login", json={"username": "가나다라", "password": " ab "}).status_code, 200)

    def test_malformed_json_and_token_validation_no_leak(self):
        response = self.client.post(PREFIX + "/login", content='{"password":"secret-value",', headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("secret-value", response.text)
        for endpoint in ("refresh", "logout"):
            for body in ({}, {"refresh_token": None}, {"refresh_token": ""}, {"refresh_token": 42}):
                self.assertEqual(self.client.post(PREFIX + "/" + endpoint, json=body).status_code, 422)

    def test_login_failure_does_not_reveal_username(self):
        self.signup()
        missing = self.client.post(PREFIX + "/login", json={**LOGIN, "username": "missing"})
        wrong = self.client.post(PREFIX + "/login", json={**LOGIN, "password": "wrong-pass"})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json(), wrong.json())

    def test_refresh_logout_and_other_session(self):
        self.signup()
        first, other = self.login(), self.login()
        self.assertEqual(first["access_expires_in"], 900)
        self.assertEqual(first["refresh_expires_in"], 28800)
        self.assertEqual(self.protected(first["access_token"]).status_code, 200)
        refresh = self.client.post(PREFIX + "/refresh", json={"refresh_token": first["refresh_token"]})
        self.assertEqual(refresh.status_code, 200)
        new = refresh.json()
        self.assertNotEqual(new["refresh_token"], first["refresh_token"])
        self.assertNotEqual(new["access_token"], first["access_token"])
        self.assertEqual(self.protected(first["access_token"]).status_code, 200)
        self.assertEqual(self.client.post(PREFIX + "/refresh", json={"refresh_token": first["refresh_token"]}).status_code, 401)
        response = self.client.post(PREFIX + "/logout", json={"refresh_token": new["refresh_token"]})
        self.assertEqual((response.status_code, response.content), (204, b""))
        for token in (first["access_token"], new["access_token"]):
            self.assertEqual(self.protected(token).status_code, 401)
        self.assertEqual(self.client.post(PREFIX + "/refresh", json={"refresh_token": new["refresh_token"]}).status_code, 401)
        self.assertEqual(self.protected(other["access_token"]).status_code, 200)
        self.assertEqual(self.client.post(PREFIX + "/logout", json={"refresh_token": new["refresh_token"]}).status_code, 204)
        self.login()

    def test_refresh_fixed_deadline_and_expiration(self):
        self.signup()
        now = int(time.time())
        self.service.clock = lambda: now
        first = self.login()
        now += 900
        refreshed = self.service.refresh(first["refresh_token"])
        self.assertEqual(refreshed.refresh_expires_in, 27900)
        now += 27840
        near_end = self.service.refresh(refreshed.refresh_token)
        self.assertEqual((near_end.access_expires_in, near_end.refresh_expires_in), (60, 60))
        now += 60
        with self.assertRaises(APIError):
            self.service.refresh(near_end.refresh_token)
        with self.assertRaises(APIError):
            self.service.authenticate(first["access_token"])

    def test_access_expired_refresh_still_valid_and_logout_without_access(self):
        self.signup()
        first = self.login()
        self.service.clock = lambda: time.time() + 901
        self.assertEqual(self.protected(first["access_token"]).status_code, 401)
        self.assertEqual(self.client.post(PREFIX + "/logout", json={"refresh_token": first["refresh_token"]}).status_code, 204)
        self.assertEqual(self.client.post(PREFIX + "/refresh", json={"refresh_token": first["refresh_token"]}).status_code, 401)

    def test_access_claims_and_token_types_rejected(self):
        self.signup()
        first = self.login()
        original = jwt.decode(first["access_token"], options={"verify_signature": False})
        self.assertEqual(self.client.get("/_test/protected").status_code, 401)
        for token in ("broken", first["refresh_token"]):
            self.assertEqual(self.protected(token).status_code, 401)
        self.assertEqual(self.client.post(PREFIX + "/refresh", json={"refresh_token": first["access_token"]}).status_code, 401)
        changes = [
            {"iss": "wrong"}, {"aud": "wrong"}, {"server_run_id": -1},
            {"token_use": "refresh"}, {"exp": int(time.time()) - 1},
            {"sid": "missing"}, {"sub": "2"}, {"iat": int(time.time()) + 60},
        ]
        for change in changes:
            token = jwt.encode({**original, **change}, self.service.signer._key, algorithm=ALGORITHM)
            self.assertEqual(self.protected(token).status_code, 401)
        for field in original:
            claims = {key: value for key, value in original.items() if key != field}
            token = jwt.encode(claims, self.service.signer._key, algorithm=ALGORITHM)
            self.assertEqual(self.protected(token).status_code, 401)
        forged = jwt.encode(original, b"x" * 32, algorithm=ALGORITHM)
        self.assertEqual(self.protected(forged).status_code, 401)

    def test_concurrent_signup(self):
        barrier = Barrier(2)

        def attempt(index):
            barrier.wait()
            return self.client.post(PREFIX + "/signup", json={**SIGNUP, "username": "admin" + str(index)}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(statuses), [201, 409])

    def test_concurrent_refresh_only_one_success(self):
        self.signup()
        first = self.login()
        barrier = Barrier(2)

        def attempt(_):
            barrier.wait()
            return self.client.post(PREFIX + "/refresh", json={"refresh_token": first["refresh_token"]}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(statuses), [200, 401])

    def test_logout_and_refresh_serialization(self):
        self.signup()
        first = self.login()
        barrier = Barrier(2)

        def logout():
            barrier.wait()
            self.service.logout(first["refresh_token"])

        def refresh():
            barrier.wait()
            try:
                return self.service.refresh(first["refresh_token"])
            except APIError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            logout_future = pool.submit(logout)
            refresh_future = pool.submit(refresh)
            logout_future.result()
            result = refresh_future.result()
        # 갱신이 먼저 완료되면 클라이언트는 명세대로 최신 Refresh로 로그아웃해야 한다.
        if result is not None:
            self.service.logout(result.refresh_token)
        self.assertEqual(self.protected(first["access_token"]).status_code, 401)
        self.assertEqual(len(self.service._sessions), 0)

    def test_signup_rollback(self):
        original_add = self.service.repository.add

        def fail_after_insert(*args):
            original_add(*args)
            raise RuntimeError("private database details")

        with patch.object(self.service.repository, "add", side_effect=fail_after_insert):
            response = self.client.post(PREFIX + "/signup", json=SIGNUP)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private database details", response.text)
        self.signup()

    def test_duplicate_instance_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "already running"):
            with TestClient(create_app(self.db_path)):
                pass
        self.signup()

    def test_health_docs_openapi(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/docs").status_code, 200)
        paths = create_app(self.db_path).openapi()["paths"]
        self.assertEqual(set(paths), {"/health", "/api/v1/cameras", "/api/v1/cameras/{camera_id}",
                                    "/api/v1/global-persons", "/api/v1/global-persons/{global_person_id}/last-seen",
                                    "/api/v1/events", "/api/v1/events/{event_id}",
                                    "/api/v1/events/{event_id}/media", "/api/v1/events/{event_id}/cnn-result", "/api/v1/settings/storage",
                                    *(PREFIX + "/" + name for name in ("signup-status", "signup", "login", "logout", "refresh"))})
        self.assertEqual(DEFAULT_DB_PATH, Path(__file__).resolve().parents[1] / "data" / "ai_cctv.db")


class LifecycleTests(unittest.TestCase):
    def test_restart_invalidates_tokens_and_retains_account(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "auth.db"
            first_app = create_app(path)
            with TestClient(first_app) as client:
                client.post(PREFIX + "/signup", json=SIGNUP)
                tokens = client.post(PREFIX + "/login", json=LOGIN).json()
                first_run = first_app.state.auth_service.run_id
            with closing(sqlite3.connect(path)) as db:
                status, ended = db.execute("SELECT status, ended_at FROM server_runs").fetchone()
                self.assertEqual(status, "stopped")
                self.assertIsNotNone(ended)
                db.execute("INSERT INTO server_runs (started_at,status) VALUES (?,?)", ("2026-10-06T00:00:00.000000+00:00", "running"))
                db.commit()
            second_app = create_app(path)
            with TestClient(second_app) as client:
                self.assertEqual(client.get(PREFIX + "/signup-status").json(), {"signup_required": False})
                self.assertGreater(second_app.state.auth_service.run_id, first_run)
                self.assertEqual(client.post(PREFIX + "/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code, 401)
                with self.assertRaises(APIError):
                    second_app.state.auth_service.authenticate(tokens["access_token"])
                self.assertEqual(client.post(PREFIX + "/login", json=LOGIN).status_code, 200)
                self.assertEqual(client.post(PREFIX + "/signup", json=SIGNUP).status_code, 409)
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute("SELECT status, ended_at FROM server_runs WHERE server_run_id=2").fetchone(), ("interrupted", None))

    def test_live_uvicorn_http(self):
        with TemporaryDirectory() as temp:
            app = create_app(Path(temp) / "live.db")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
                config = uvicorn.Config(app, log_level="critical", access_log=False)
                server = uvicorn.Server(config)
                thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
                thread.start()
                try:
                    deadline = time.monotonic() + 10
                    while not server.started and thread.is_alive() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(server.started)
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                        self.assertEqual(client.get("/health").status_code, 200)
                        self.assertIn("swagger-ui", client.get("/docs").text)
                        self.assertIn(PREFIX + "/login", client.get("/openapi.json").json()["paths"])
                        self.assertEqual(client.get(PREFIX + "/signup-status").json(), {"signup_required": True})
                        self.assertEqual(client.post(PREFIX + "/signup", json=SIGNUP).status_code, 201)
                        self.assertEqual(client.get(PREFIX + "/signup-status").json(), {"signup_required": False})
                        tokens = client.post(PREFIX + "/login", json=LOGIN).json()
                        refreshed = client.post(PREFIX + "/refresh", json={"refresh_token": tokens["refresh_token"]})
                        self.assertEqual(refreshed.status_code, 200)
                        body = {"refresh_token": refreshed.json()["refresh_token"]}
                        self.assertEqual(client.post(PREFIX + "/logout", json=body).status_code, 204)
                        self.assertEqual(client.post(PREFIX + "/refresh", json=body).status_code, 401)
                finally:
                    server.should_exit = True
                    thread.join(timeout=10)
                self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
