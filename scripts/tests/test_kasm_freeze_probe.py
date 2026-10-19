"""Unit tests for scripts/spike/kasm-freeze-probe.py (the #159 freeze spike).

Only the pure parts are tested, which is the whole point of having them pure:
nothing here talks to Kasm, to docker or to the network, so this runs in CI
against code that was written for a disposable lab install.

What is under test is the three ways this probe could quietly produce a WRONG
number or leak something into a committed log:

  1. redaction -- one secret field left out of SECRET_FIELDS and a token or a
     student link ends up in a public repo (the repo is public; this is the
     only barrier between the lab's credentials and it);
  2. read_status -- Kasm answers get_kasm_status in two different shapes and
     reading only one of them is what made test F report "None == None" for a
     day, and report a paused session as having no status at all;
  3. parse_docker_stats -- silently returning None or the limit instead of the
     usage would put a wrong capacity figure into docs/adr and
     docs/HOSTING_SIZING.md.

The hyphen in the script's filename means it is loaded by path, same technique
as test_create_session.py.
"""
import importlib.util
import json
import os
import sys

_MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "spike", "kasm-freeze-probe.py")
_spec = importlib.util.spec_from_file_location("kasm_freeze_probe", _MODULE_PATH)
probe = importlib.util.module_from_spec(_spec)
sys.modules["kasm_freeze_probe"] = probe
_spec.loader.exec_module(probe)

# A synthetic bearer token. Deliberately NOT shaped like a real JWT: a
# three-segment eyJ... string in a public repo trips secret scanners for no
# benefit, and nothing here parses the token format.
JWT = "sess-fake-bearer-token-value-0123456789"
LINK = "https://localhost/#/connect/kasm/kasm-uuid/user-uuid/" + JWT


class TestRedact:
    def test_secrets_never_survive(self):
        out = json.dumps(probe.redact({
            "token": JWT,
            "kasm_url": LINK,
            "session_token": JWT,
            "api_key_secret": "super-secret-value",
            "connection_info": {"password": "hunter2"},
        }))
        for secret in (JWT, "super-secret-value", "hunter2", "kasm-uuid"):
            assert secret not in out

    def test_secrets_are_replaced_by_comparable_fingerprints(self):
        first = probe.redact({"kasm_url": LINK})
        same = probe.redact({"kasm_url": LINK})
        other = probe.redact({"kasm_url": LINK + "?x=1"})
        assert first == same
        assert first["kasm_url_fp"] != other["kasm_url_fp"]

    def test_the_shape_that_is_the_whole_point_is_kept(self):
        body = probe.redact({"current_time": "x", "kasm": {"operational_status": "running"}})
        assert body["kasm"]["operational_status"] == "running"

    def test_identifiers_are_fingerprinted_not_stored(self):
        out = probe.redact({"kasm_id": "0b0e6d4c-0000-4000-8000-000000000001",
                            "user_id": "0b0e6d4c-0000-4000-8000-000000000002"})
        assert "0b0e6d4c" not in json.dumps(out)
        assert sorted(out) == ["kasm_id_fp", "user_id_fp"]

    def test_lists_are_capped_and_blobs_truncated(self):
        out = probe.redact({"kasms": list(range(50)), "note": "y" * 500})
        assert len(out["kasms"]) == 10
        assert out["note"].endswith("...") and len(out["note"]) < 210


class TestReadStatus:
    RUNNING = {"current_time": "2026-10-16 08:50:01",
               "kasm": {"operational_status": "running", "container_id": "abc"},
               "kasm_url": LINK}
    PAUSED = {"operational_status": "paused"}

    def test_running_shape(self):
        read = probe.read_status(self.RUNNING)
        assert read["operational_status"] == "running"
        assert read["kasm_url_present"] is True
        assert read["has_kasm_object"] is True

    def test_paused_reports_its_state_where_the_nested_read_saw_nothing(self):
        # The 2026-10-15 conclusion "a paused session reports no status and no
        # link" came from body["kasm"]["operational_status"]; the paused answer
        # has no kasm object at all, so that read returns nothing while the
        # state is sitting in the top-level key.
        body = self.PAUSED
        try:
            body["kasm"]["operational_status"]
            raise AssertionError("fixture no longer reproduces the old read")
        except (KeyError, TypeError):
            pass
        read = probe.read_status(body)
        assert read["operational_status"] == "paused"
        assert read["kasm_url_present"] is False

    def test_the_link_is_reported_absent_rather_than_empty(self):
        assert probe.read_status(self.PAUSED)["kasm_url_fp"] is None
        assert probe.read_status(self.PAUSED)["kasm_url_fp"] != probe.fingerprint("")

    def test_unexpected_answers_are_a_result_not_a_crash(self):
        assert probe.read_status("<html>500</html>")["ok"] is False
        assert probe.read_status(None)["ok"] is False


class TestParseDockerStats:
    def test_usage_not_the_limit(self):
        row = probe.parse_docker_stats("adminkasm.lo_0b0e6d4c\t92.81MiB / 2GiB\t0.00%")
        assert row["mem_mib"] == 92.81
        assert row["cpu_pct"] == 0.0

    def test_units(self):
        assert probe.parse_docker_stats("a\t1.5GiB / 4GiB\t10%")["mem_mib"] == 1536.0
        assert probe.parse_docker_stats("a\t512KiB / 4GiB\t10%")["mem_mib"] == 0.5

    def test_container_name_is_not_stored_verbatim(self):
        # The name embeds the first 8 characters of the kasm_id, which is an
        # identifier; the appendix had to be fixed for exactly this.
        row = probe.parse_docker_stats("adminkasm.lo_0b0e6d4c\t92MiB / 2GiB\t0%")
        assert "0b0e6d4c" not in row["name"]

    def test_non_rows_return_none(self):
        assert probe.parse_docker_stats("") is None
        assert probe.parse_docker_stats("only-one-field") is None

    def test_unparseable_cpu_is_none_not_zero(self):
        assert probe.parse_docker_stats("a\t92MiB / 2GiB\t-")["cpu_pct"] is None


class TestSecretFieldList:
    def test_fields_seen_in_real_kasm_responses_are_covered(self):
        observed = ["token", "session_token", "kasm_url", "api_key", "api_key_secret",
                    "container_id", "user_id", "kasm_id", "image_id", "connection_info",
                    "connection_credential", "view_only_token", "share_id", "pw_hash", "salt"]
        for field in observed:
            assert field in probe.SECRET_FIELDS or field in probe.ID_FIELDS, field
