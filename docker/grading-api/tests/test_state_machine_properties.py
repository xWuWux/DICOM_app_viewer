"""Property-based tests for the 3-stage grading state machine (issue #238 F).

test_state_machine.py enumerates the transitions its author thought of.
These tests assert the INVARIANTS that must hold after ANY legal action
sequence -- the class of bug the case-list can miss (an interaction only
reachable on, say, the third case of assessment after a reset).

Design notes (from the p2 design review, all applied):
  * One RuleBasedStateMachine instance per example -> setup()/teardown()
    build a FRESH sqlite file + TestClient per example, the same way the
    function-scoped `client` fixture in conftest.py does it, but by hand.
    A function-scoped fixture would share one DB across every example
    (HealthCheck.function_scoped_fixture exists exactly for that bug --
    it is NOT suppressed here; the design avoids it instead).
  * db.now is hand-patched to a mutable clock advanced only by rules, so
    every submission's server-side time_spent_seconds (issue #29: the
    client can never send or influence it) is exactly the clock delta
    since case assignment -- asserted against the stored row, not the
    response.
  * RESET_ENABLED is set explicitly per machine, never inherited.
  * /orthanc-authz is exercised in-process (X-Original-URI with
    sha256(token), see tests/test_orthanc_authz.py): on the test stage
    only the CURRENT case's study is allowed, every previously-seen uid
    must 403; on learning/assessment every browsable uid must 204.
  * Ground truth is asserted POSITIVELY: /case keys must be a SUBSET of
    the exposed-set below -- "absence of correct_category" would be
    vacuous because no such key ever exists; the real names are
    correct / ground_truth_category / ground_truth_modifier_s and they
    appear only in /submit feedback payloads.

CI determinism: `ci` profile = derandomized, modest example counts,
deadline=None (SQLite I/O jitter on shared runners). Selected via the CI
env var, so local runs stay exploratory.
"""

import hashlib
import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, initialize, invariant, rule

from app import db as db_module
from app import main as main_module
from app.main import app

if os.environ.get("CI"):
    settings.register_profile(
        "ci",
        derandomize=True,
        deadline=None,
        max_examples=30,
        stateful_step_count=12,
    )
    settings.load_profile("ci")
else:
    settings.register_profile(
        "dev",
        deadline=None,
        max_examples=50,
        stateful_step_count=20,
        suppress_health_check=[HealthCheck.too_slow],
    )
    settings.load_profile("dev")

STAGE_RANK = {"learning": 0, "assessment": 1, "test": 2, "complete": 3}

# Everything /case may legitimately expose (p2 review point 3: assert a
# SUBSET of this, never the absence of a made-up key).
CASE_ALLOWED_KEYS = {
    "complete",
    "case_id",
    "stage",
    "title",
    "orthanc_study_uid",
    "position",
    "total_in_stage",
    "category_options",
    "timer",
    "browsable",
    "view_case_id",
    "view_study_uid",
}

COHORT_KEY = {"X-Coordinator-Key": os.environ["GRADING_COORDINATOR_KEY"]}

# module-level: subclass bodies cannot see a parent class body's names,
# and rules in _ResetOnMachine take the bundle as an argument
TOKENS = Bundle("tokens")


class _Harness:
    """One fresh app+DB+clock; shared by the stateful machine and the
    data-property tests. Restores every module global it patches."""

    def __init__(self, *, reset_enabled: bool):
        self.dir = tempfile.TemporaryDirectory(prefix="grading-prop-")
        self.db_path = os.path.join(self.dir.name, "grading.db")
        self.clock = [1_700_000_000.0]
        self._saved = (db_module.DB_PATH, db_module.now, main_module.RESET_ENABLED)
        db_module.DB_PATH = self.db_path
        db_module.now = lambda: self.clock[0]
        main_module.RESET_ENABLED = reset_enabled
        self.client = TestClient(app)
        self.client.__enter__()  # lifespan -> db.init_db() against the patched path

    def close(self):
        # teardown must restore globals even when a rule already failed
        try:
            self.client.__exit__(None, None, None)
        finally:
            db_module.DB_PATH, db_module.now, main_module.RESET_ENABLED = self._saved
            self.dir.cleanup()

    def mint(self, student_id, session_id="sess-prop"):
        resp = self.client.post(
            "/session",
            json={"student_id": student_id, "session_id": session_id},
            headers=COHORT_KEY,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["token"]

    def case(self, token):
        return self.client.get("/case", headers={"X-Grading-Token": token})

    def authz(self, token, uid):
        key = hashlib.sha256(token.encode()).hexdigest()
        return self.client.get(
            "/orthanc-authz",
            headers={"X-Original-URI": f"/s/{key}/dicom-web/studies/{uid}/series"},
        )

    def stored_time_spent(self, student_id):
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT time_spent_seconds FROM submissions WHERE student_id = ? ORDER BY rowid",
                (student_id,),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()


class _GradingMachine(RuleBasedStateMachine):
    """Mirrors the model state a student can observe and checks the
    invariants after EVERY action."""

    tokens = TOKENS

    def __init__(self):
        super().__init__()
        self.h = _Harness(reset_enabled=self._reset_enabled)
        self.model = {}  # token -> dict(stage_rank, position, case_id, uid, prev_uids, view_set)

    @initialize(target=TOKENS)
    def new_session(self):
        self._n = getattr(self, "_n", 0) + 1
        student = f"prop-stu-{self._n}"
        token = self.h.mint(student)
        data = self.h.case(token).json()
        self.model[token] = {
            "student": student,
            "stage": 0,  # STAGE_RANK["learning"], kept as rank (invariant compares ranks)
            "position": data["position"],
            "case_id": data["case_id"],
            "uid": data["orthanc_study_uid"],
            "seen_uids": {data["orthanc_study_uid"]},
            "view_pending": False,
            "clock_at_assign": self.h.clock[0],
        }
        return token

    @rule(token=TOKENS)
    def advance_case(self, token):
        """Submit the CURRENT case with a legal body built from the live
        /case shape; assert stage/position move exactly as the model says
        and the feedback payload is exactly what the contract allows."""
        m = self.model[token]
        live = self.h.case(token)
        assert live.status_code == 200
        data = live.json()
        if data.get("complete"):
            # /case for a finished session is EXACTLY {"complete": true};
            # any submit attempt is 409 STAGE_MISMATCH, never 5xx or a
            # silent mutation.
            assert set(data) == {"complete"}
            resp = self.h.client.post(
                "/submit",
                json={"token": token, "case_id": data.get("case_id", 1), "stage": "test", "category": "2"},
            )
            assert resp.status_code == 409
            assert resp.json().get("error_code") == "VALIDATION_STAGE_MISMATCH"
            assert self.h.case(token).json() == {"complete": True}
            return
        stage = data["stage"]
        body = {"token": token, "case_id": data["case_id"], "stage": stage}
        if stage == "learning":
            body["text"] = "prop answer"
        else:
            body["category"] = "2"
            body["modifier_s"] = False
        before_rank = STAGE_RANK[stage]
        before_pos = data["position"]
        self.h.clock[0] += 7.0  # deterministic "student thought" time
        resp = self.h.client.post("/submit", json=body)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        if stage == "learning":
            assert set(payload) == {"reference_report"}, payload
        elif stage == "assessment":
            assert set(payload) <= {
                "correct",
                "ground_truth_category",
                "ground_truth_modifier_s",
                "reference_report",
            }, payload
            assert "correct" in payload
        else:  # test stage: the answer must reveal NOTHING
            assert payload == {}, payload
        after = self.h.case(token).json()
        after_rank = STAGE_RANK["complete"] if after.get("complete") else STAGE_RANK[after["stage"]]
        assert (after_rank, after.get("position", 0)) >= (before_rank, before_pos) or after_rank > before_rank, (
            "stage regressed or position rewound"
        )
        m["clock_at_assign"] = self.h.clock[0]
        m["seen_uids"].add(after.get("orthanc_study_uid") or data["orthanc_study_uid"])
        m["view_pending"] = False  # every accepted submit clears the pointer

    @rule(token=TOKENS)
    def resubmit_stale(self, token):
        """Replay the last accepted case_id: 409, progress unchanged."""
        m = self.model[token]
        data = self.h.case(token).json()
        if data.get("complete"):
            return
        stale_body = {
            "token": token,
            "case_id": data["case_id"],
            "stage": data["stage"],
            "text": "x",
            "category": "1",
            "modifier_s": False,
        }
        first = self.h.client.post("/submit", json=stale_body)
        assert first.status_code == 200  # the legitimate submit advances...
        snap = self.h.case(token).json()
        if snap.get("complete"):
            m["clock_at_assign"] = self.h.clock[0]
            m["view_pending"] = False
            return
        second = self.h.client.post("/submit", json=stale_body)
        assert second.status_code == 409, second.status_code  # ...its replay does not
        after = self.h.case(token).json()
        assert after["position"] == snap["position"]
        assert after["stage"] == snap["stage"]
        m["clock_at_assign"] = self.h.clock[0]
        m["view_pending"] = False

    @rule(token=TOKENS, extra=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False))
    def time_is_server_side(self, token, extra):
        """Whatever wall time passes (clock jitter only -- SubmitBody has
        NO time_spent_seconds field since #29; Pydantic drops such unknown
        keys), the STORED value equals the clock delta since assignment."""
        m = self.model[token]
        data = self.h.case(token).json()
        if data.get("complete"):
            return
        self.h.clock[0] += extra
        body = {
            "token": token,
            "case_id": data["case_id"],
            "stage": data["stage"],
            "time_spent_seconds": 99999,
        }  # unknown field: must be dropped
        if data["stage"] == "learning":
            body["text"] = "t"
        else:
            body["category"] = "2"
        delta = self.h.clock[0] - m["clock_at_assign"]
        resp = self.h.client.post("/submit", json=body)
        assert resp.status_code == 200
        stored = self.h.stored_time_spent(m["student"])[-1]
        assert stored == max(0, delta), (stored, delta)
        m["clock_at_assign"] = self.h.clock[0]
        m["view_pending"] = False

    @rule(token=TOKENS)
    def authz_tracks_stage(self, token):
        """Learning/assessment: every browsable study opens (204). Test:
        ONLY the current one -- every previously-seen uid must be 403."""
        m = self.model[token]
        data = self.h.case(token).json()
        if data.get("complete"):
            return
        if data["stage"] == "test":
            assert self.h.authz(token, data["orthanc_study_uid"]).status_code == 204
            for uid in m["seen_uids"]:
                if uid != data["orthanc_study_uid"]:
                    assert self.h.authz(token, uid).status_code == 403, uid
        else:
            # /studies deliberately exposes NO uids (titles/positions only)
            # -- so the uid-level claims testable in-process are: the
            # CURRENT study opens, and while a view pointer exists, ITS
            # study opens too (that is the pointer's whole purpose).
            assert self.h.authz(token, data["orthanc_study_uid"]).status_code == 204
            if "view_study_uid" in data:
                assert self.h.authz(token, data["view_study_uid"]).status_code == 204

    @rule(token=TOKENS)
    def view_respects_stage(self, token):
        """POST /view: browsable stages may point at another browsable
        case (and the pointer shows up in /case); the test stage always
        403s."""
        m = self.model[token]
        data = self.h.case(token).json()
        if data.get("complete"):
            return
        studies = self.h.client.get("/studies", headers={"X-Grading-Token": token}).json()
        others = [i for i in studies.get("items", []) if i["case_id"] != data["case_id"]]
        if data["stage"] == "test":
            target = others[0] if others else {"case_id": data["case_id"]}
            resp = self.h.client.post("/view", json={"token": token, "case_id": target["case_id"]})
            assert resp.status_code == 403
            return
        if not studies["browsable"] or not others:
            return
        target = others[0]
        resp = self.h.client.post("/view", json={"token": token, "case_id": target["case_id"]})
        assert resp.status_code == 200, resp.text
        after = self.h.case(token).json()
        assert after.get("view_case_id") == target["case_id"]
        m["view_pending"] = True

    @rule(token=TOKENS)
    def reset_per_flag(self, token):
        """With RESET_ENABLED off (the shipped default) /reset is 403
        RESET_DISABLED and NOTHING changes -- including on the test stage,
        where the on-machine's refusal test lives instead."""
        if self._reset_enabled:
            return
        snap = self.h.case(token).json()
        if snap.get("complete"):
            return
        resp = self.h.client.post("/reset", json={"token": token})
        assert resp.status_code == 403
        assert resp.json().get("error_code") == "RESET_DISABLED"
        after = self.h.case(token).json()
        assert after.get("stage", "complete" if snap.get("complete") else "?") == snap["stage"]

    @invariant()
    def case_shape_is_conformant(self):
        for token, m in self.model.items():
            resp = self.h.case(token)
            if resp.status_code == 401:
                continue  # superseded token (revoked by a later /session)
            assert resp.status_code == 200
            data = resp.json()
            assert set(data) <= CASE_ALLOWED_KEYS, set(data) - CASE_ALLOWED_KEYS
            stage = "complete" if data.get("complete") else data["stage"]
            assert STAGE_RANK[stage] >= m["stage"], "stage regressed across steps"
            m["stage"] = STAGE_RANK[stage]
            if stage == "test":
                assert "timer" in data, "test stage without timer"
                assert data["browsable"] is False
            else:
                assert "timer" not in data
            if m["view_pending"]:
                assert "view_case_id" in data
            else:
                assert "view_case_id" not in data and "view_study_uid" not in data, (
                    "view pointer survived a transition it should have cleared"
                )

    def teardown(self):
        self.h.close()


class _ResetOffMachine(_GradingMachine):
    _reset_enabled = False


class _ResetOnMachine(_GradingMachine):
    """RESET_ENABLED=True: /reset from learning/assessment returns to
    learning position 1, from the test stage it is 403
    RESET_BLOCKED_DURING_TEST (the shared reset_per_flag rule flips to
    allow-mode here)."""

    _reset_enabled = True

    @rule(token=TOKENS)
    def reset_when_allowed(self, token):
        data = self.h.case(token).json()
        resp = self.h.client.post("/reset", json={"token": token})
        if not data.get("complete") and data["stage"] == "test":
            assert resp.status_code == 403
            assert resp.json().get("error_code") == "RESET_BLOCKED_DURING_TEST"
            return
        # complete is NOT the test stage: a finished student may start over
        # (measured: 200, progress wiped to learning case 1)
        assert resp.status_code == 200, resp.text
        after = self.h.case(token).json()
        assert after["stage"] == "learning" and after["position"] == 1
        # a reset legitimately rewinds POSITION inside/below the current
        # rank; the invariant tracker must accept that one sanctioned jump
        self.model[token]["stage"] = 0
        # and it clears the view pointer like any transition (measured:
        # /case after a reset carries no view_case_id)
        self.model[token]["view_pending"] = False


TestGradingMachineResetOff = _ResetOffMachine.TestCase
TestGradingMachineResetOn = _ResetOnMachine.TestCase
