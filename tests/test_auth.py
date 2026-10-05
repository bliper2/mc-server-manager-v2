import json
import time
import unittest

from manager import auth, totp
from tests.support import HOME, OWNER, AppTestCase


class SetupAndSessions(AppTestCase):
    def test_api_needs_sign_in(self):
        self.assertEqual(self.client().get("/api/servers").status_code, 401)

    def test_login_page_before_setup(self):
        page = self.client().get("/")
        self.assertIn(b"Create the owner account", page.data)

    def test_setup_rules(self):
        c = self.client()
        self.assertEqual(c.post("/api/auth/setup", json={"username": "owner", "password": "short"}).status_code, 400)
        proxied = c.post("/api/auth/setup", json={"username": "owner", "password": "correct horse"}, headers={"X-Forwarded-For": "100.64.0.5"})
        self.assertEqual(proxied.status_code, 403, "setup must be refused through a proxy or Tailscale")
        self.assertEqual(c.post("/api/auth/setup", json={"username": "owner", "password": "correct horse"}).status_code, 200)
        self.assertEqual(c.post("/api/auth/setup", json={"username": "second", "password": "correct horse"}).status_code, 409)

    def test_password_is_hashed(self):
        self.owner()
        stored = json.loads((HOME / "staff.json").read_text())
        self.assertNotIn(OWNER[1], json.dumps(stored))
        self.assertTrue(stored[0]["password_hash"].startswith(("scrypt:", "pbkdf2:")))

    def test_wrong_login_and_unknown_user_look_the_same(self):
        owner = self.owner()
        self.staff(owner)
        wrong = self.client().post("/api/auth/login", json={"username": "alice", "password": "nope"})
        unknown = self.client().post("/api/auth/login", json={"username": "nobody", "password": "nope"})
        self.assertEqual((wrong.status_code, wrong.get_json()["error"]), (unknown.status_code, unknown.get_json()["error"]))

    def test_lockout_after_five_failures(self):
        owner = self.owner()
        self.staff(owner)
        c = self.client()
        codes = [c.post("/api/auth/login", json={"username": "alice", "password": f"bad{i}"}).status_code for i in range(7)]
        self.assertEqual(codes, [401] * 5 + [429] * 2)
        self.assertEqual(c.post("/api/auth/login", json={"username": "alice", "password": "alicepass1"}).status_code, 429)

    def test_password_change_and_reset_end_sessions(self):
        owner = self.owner()
        bob = self.staff(owner, "bob", "bobpass12")
        self.assertEqual(bob.get("/api/servers").status_code, 200)
        self.assertEqual(owner.post("/api/staff/bob/password", json={"password": "bobnew1234"}).status_code, 200)
        self.assertEqual(bob.get("/api/servers").status_code, 401)
        self.assertEqual(owner.post("/api/staff/bob/delete").status_code, 200)
        self.assertEqual(self.client().post("/api/auth/login", json={"username": "bob", "password": "bobnew1234"}).status_code, 401)

    def test_own_password_change_keeps_session(self):
        owner = self.owner()
        alice = self.staff(owner)
        self.assertEqual(alice.post("/api/auth/password", json={"current": "wrong", "new": "newpass123"}).status_code, 400)
        self.assertEqual(alice.post("/api/auth/password", json={"current": "alicepass1", "new": "newpass123"}).status_code, 200)
        self.assertEqual(alice.get("/api/servers").status_code, 200)

    def test_owner_plus_two_staff_only(self):
        owner = self.owner()
        self.staff(owner, "alice", "alicepass1")
        self.staff(owner, "bob", "bobpass12")
        self.assertEqual(owner.post("/api/staff", json={"username": "carol", "password": "carolpass1"}).status_code, 409)
        self.assertEqual(owner.post("/api/staff", json={"username": "ALICE", "password": "whatever123"}).status_code, 409)
        listing = owner.get("/api/staff").get_json()
        self.assertEqual(len(listing["accounts"]), 3)
        self.assertNotIn("password_hash", json.dumps(listing))
        self.assertEqual(owner.post("/api/staff/owner/delete").status_code, 404)


class Permissions(AppTestCase):
    def test_staff_cannot_manage_accounts_or_updates(self):
        alice = self.staff(self.owner())
        self.assertEqual(alice.get("/api/staff").status_code, 403)
        self.assertEqual(alice.post("/api/staff", json={"username": "evil", "password": "evilpass12"}).status_code, 403)
        self.assertEqual(alice.post("/api/manager/update/apply", json={}).status_code, 403)
        self.assertEqual(alice.get("/api/notifications").status_code, 403)
        self.assertEqual(alice.post("/api/manager/restart", json={}).status_code, 403)

    def test_permissions_gate_changes_but_not_reading(self):
        from tests.support import make_server
        make_server()
        owner = self.owner()
        alice = self.staff(owner, permissions=["backups"])
        self.assertEqual(alice.get("/api/servers").status_code, 200)
        self.assertEqual(alice.post("/api/server/alpha_1/start", json={}).status_code, 403)
        self.assertEqual(alice.post("/api/server/alpha_1/command", json={"command": "list"}).status_code, 403)
        self.assertEqual(alice.post("/api/server/alpha_1/delete").status_code, 403)
        self.assertNotEqual(alice.post("/api/server/alpha_1/backups/create", json={}).status_code, 403)
        owner.post("/api/staff/alice/permissions", json={"permissions": ["backups", "control"]})
        self.assertNotEqual(alice.post("/api/server/alpha_1/start", json={}).status_code, 403)

    def test_staff_from_before_permissions_existed_keep_full_access(self):
        owner = self.owner()
        self.staff(owner)
        accounts = json.loads((HOME / "staff.json").read_text())
        for account in accounts:
            account.pop("permissions", None)
        (HOME / "staff.json").write_text(json.dumps(accounts))
        listing = owner.get("/api/staff").get_json()["accounts"]
        legacy = next(a for a in listing if a["username"] == "alice")
        self.assertEqual(sorted(legacy["permissions"]), sorted(auth.ALL_PERMISSIONS))

    def test_unknown_permission_rejected(self):
        owner = self.owner()
        self.assertEqual(owner.post("/api/staff", json={"username": "zed", "password": "zedpass123", "permissions": ["root"]}).status_code, 400)

    def test_every_write_endpoint_is_classified(self):
        """A new POST route must be given a permission in auth.ENDPOINT_RULES, or it is refused to everyone."""
        writes = set()
        for rule in self.app.url_map.iter_rules():
            if rule.rule.startswith("/api/") and rule.rule not in auth.PUBLIC_API and rule.methods - {"GET", "HEAD", "OPTIONS"}:
                writes.add(rule.endpoint)
        self.assertEqual(sorted(writes - set(auth.ENDPOINT_RULES)), [], "unclassified write endpoints")
        self.assertEqual(sorted(set(auth.ENDPOINT_RULES) - {r.endpoint for r in self.app.url_map.iter_rules()}), [], "rules for missing endpoints")

    def test_private_files_and_passwords_stay_owner_only(self):
        from tests.support import make_server
        folder = make_server()
        (folder / "manager_meta.json").write_text(json.dumps({"name": "x", "playit_secret": "TOPSECRET", "rcon": {"enabled": True, "port": 25575, "password": "rconpw"}}))
        owner = self.owner()
        alice = self.staff(owner, permissions=["files"])
        self.assertEqual(alice.get("/api/server/alpha_1/fs/read?path=manager_meta.json").status_code, 403)
        self.assertEqual(alice.get("/api/server/alpha_1/fs/download?path=manager_meta.json").status_code, 404)
        self.assertEqual(owner.get("/api/server/alpha_1/fs/read?path=manager_meta.json").status_code, 200)
        rcon = owner.get("/api/server/alpha_1/rcon").get_json()
        self.assertNotIn("rconpw", json.dumps(rcon))
        self.assertTrue(rcon["settings"]["has_password"])


class TwoFactor(AppTestCase):
    def enroll(self, client):
        begin = client.post("/api/auth/2fa/begin").get_json()
        self.assertTrue(begin["ok"])
        self.assertTrue(begin["uri"].startswith("otpauth://totp/"))
        code = totp.code_for_step(begin["secret"], int(time.time() // 30))
        done = client.post("/api/auth/2fa/enable", json={"code": code}).get_json()
        self.assertTrue(done["ok"], done)
        return begin["secret"], done["recovery_codes"]

    def test_full_flow(self):
        owner = self.owner()
        secret, recovery = self.enroll(owner)
        self.assertEqual(len(recovery), 8)
        stored = json.loads((HOME / "staff.json").read_text())[0]
        self.assertNotIn(recovery[0], json.dumps(stored), "recovery codes must be stored hashed")

        fresh = self.client()
        step = fresh.post("/api/auth/login", json={"username": OWNER[0], "password": OWNER[1]}).get_json()
        self.assertTrue(step["totp_required"] and not step["ok"])
        self.assertEqual(fresh.get("/api/servers").status_code, 401, "password alone must not sign in")
        bad = fresh.post("/api/auth/login", json={"username": OWNER[0], "password": OWNER[1], "code": "000000"})
        self.assertEqual(bad.status_code, 401)

        # the enrollment code was already used in this window, so replay must fail; the next window works
        used = totp.code_for_step(secret, int(time.time() // 30))
        replay = fresh.post("/api/auth/login", json={"username": OWNER[0], "password": OWNER[1], "code": used})
        self.assertEqual(replay.status_code, 401, "a code can only be used once")
        later = totp.code_for_step(secret, int(time.time() // 30) + 1)
        ok = fresh.post("/api/auth/login", json={"username": OWNER[0], "password": OWNER[1], "code": later})
        self.assertTrue(ok.get_json()["ok"])

        other = self.client()
        by_recovery = other.post("/api/auth/login", json={"username": OWNER[0], "password": OWNER[1], "code": recovery[0]})
        self.assertTrue(by_recovery.get_json()["ok"])
        again = self.client().post("/api/auth/login", json={"username": OWNER[0], "password": OWNER[1], "code": recovery[0]})
        self.assertEqual(again.status_code, 401, "a recovery code works once")

    def test_disable_needs_password_and_code(self):
        owner = self.owner()
        _, recovery = self.enroll(owner)
        self.assertEqual(owner.post("/api/auth/2fa/disable", json={"password": "wrong", "code": recovery[1]}).status_code, 400)
        self.assertEqual(owner.post("/api/auth/2fa/disable", json={"password": OWNER[1], "code": "123456"}).status_code, 400)
        self.assertTrue(owner.post("/api/auth/2fa/disable", json={"password": OWNER[1], "code": recovery[1]}).get_json()["ok"])
        self.assertTrue(self.client().post("/api/auth/login", json={"username": OWNER[0], "password": OWNER[1]}).get_json()["ok"])

    def test_owner_can_reset_staff_two_factor(self):
        owner = self.owner()
        alice = self.staff(owner)
        self.enroll(alice)
        self.assertTrue(self.client().post("/api/auth/login", json={"username": "alice", "password": "alicepass1"}).get_json()["totp_required"])
        self.assertEqual(owner.post("/api/staff/alice/2fa/reset").status_code, 200)
        self.assertTrue(self.client().post("/api/auth/login", json={"username": "alice", "password": "alicepass1"}).get_json()["ok"])


class TotpPrimitives(unittest.TestCase):
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 test key "12345678901234567890"

    def test_rfc6238_vector(self):
        self.assertEqual(totp.code_for_step(self.SECRET, 59 // 30), "287082")
        self.assertEqual(totp.verify(self.SECRET, "287082", now=59), 1)

    def test_replay_and_window(self):
        self.assertIsNone(totp.verify(self.SECRET, "287082", last_step=1, now=59))
        self.assertIsNone(totp.verify(self.SECRET, "287082", now=59 + 300))
        self.assertIsNone(totp.verify(self.SECRET, "12345", now=59))


if __name__ == "__main__":
    unittest.main()
