"""Cases loaded from a local JSON file instead of the placeholder SEED_CASES.

Why a file outside the repository: the GitHub repo is public, and real cases
carry the exam's ANSWER KEY (Lung-RADS ground truth), clinical reference
reports and real study UIDs -- none of that may ever be committed. The file is
supplied at runtime (GRADING_CASES_FILE, normally /local/cases.json from the
git-ignored ./local directory), built by scripts/build-cases-file.py.

Format: a JSON list of objects
    {"stage": "learning", "order_index": 0, "orthanc_study_uid": "1.2.3",
     "title": "Przypadek 1 (nauka)", "ground_truth_category": "2",
     "ground_truth_modifier_s": 0, "reference_report": "..."}

Every validation message names the entry INDEX and the FIELD only, never the
value: this runs at startup and its output ends up in logs, and the values are
exactly what must not be logged (issue #93's lesson).
"""

import json
import re

# DICOM UI alphabet, at most 128 characters, and it must END in a digit: a
# dots-only UID (".", "..") or a trailing dot identifies nothing and was
# accepted by the first shape (issue #160 review N3). A LEADING dot stays
# tolerated on purpose -- the received real exports carry a stray one on every
# UID (issue #162), and rejecting it here would refuse our own input pipeline.
STUDY_UID_RE = re.compile(r"[0-9.]{0,127}[0-9]")
_FIELDS = {
    "stage",
    "order_index",
    "orthanc_study_uid",
    "title",
    "ground_truth_category",
    "ground_truth_modifier_s",
    "reference_report",
}


class CasesFileError(ValueError):
    """Invalid cases file. The message never contains a value from the file."""


def load_cases(path: str, stages: list[str], categories: set[str]) -> list[tuple]:
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raise CasesFileError("cases file does not exist (check the GRADING_CASES_FILE mount)") from None
    except (OSError, UnicodeDecodeError):
        raise CasesFileError("cases file cannot be read") from None
    except json.JSONDecodeError as exc:
        raise CasesFileError(f"cases file is not valid JSON (line {exc.lineno}, column {exc.colno})") from None

    if not isinstance(raw, list) or not raw:
        raise CasesFileError("cases file must be a non-empty JSON list")

    rows: list[tuple] = []
    seen: set[tuple] = set()
    for i, item in enumerate(raw):
        where = f"entry {i}"
        if not isinstance(item, dict):
            raise CasesFileError(f"{where}: must be an object")
        missing = sorted(_FIELDS - {"ground_truth_modifier_s"} - set(item))
        if missing:
            raise CasesFileError(f"{where}: missing field(s) {', '.join(missing)}")
        unknown = sorted(set(item) - _FIELDS)
        if unknown:
            raise CasesFileError(f"{where}: unknown field(s) {', '.join(unknown)}")

        if item["stage"] not in stages:
            raise CasesFileError(f"{where}: field stage is not one of the known stages")
        idx = item["order_index"]
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            raise CasesFileError(f"{where}: field order_index must be a non-negative integer")
        uid = item["orthanc_study_uid"]
        if not isinstance(uid, str) or not STUDY_UID_RE.fullmatch(uid):
            raise CasesFileError(f"{where}: field orthanc_study_uid is not a DICOM UID (digits and dots)")
        for name in ("title", "reference_report"):
            if not isinstance(item[name], str) or not item[name].strip():
                raise CasesFileError(f"{where}: field {name} must be a non-empty string")
        if item["ground_truth_category"] not in categories:
            raise CasesFileError(f"{where}: field ground_truth_category is not a known Lung-RADS category")
        mod = item.get("ground_truth_modifier_s", 0)
        if mod not in (0, 1) or isinstance(mod, bool):
            raise CasesFileError(f"{where}: field ground_truth_modifier_s must be 0 or 1")

        key = (item["stage"], idx)
        if key in seen:
            raise CasesFileError(f"{where}: duplicate (stage, order_index)")
        seen.add(key)
        rows.append(
            (item["stage"], idx, uid, item["title"], item["ground_truth_category"], mod, item["reference_report"])
        )

    for stage in stages:
        indexes = sorted(idx for s, idx in seen if s == stage)
        if not indexes:
            raise CasesFileError(f"stage {stage} has no case (every stage needs at least one, or /case answers 500)")
        if indexes != list(range(len(indexes))):
            raise CasesFileError(f"stage {stage}: order_index values must run 0..{len(indexes) - 1} without gaps")
    return rows
