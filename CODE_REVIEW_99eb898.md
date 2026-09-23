# Code Review — Commit 99eb898

**Issue #29**: Zabezpieczenie pomiaru czasu przed manipulacją po stronie klienta

**Data**: 2026-01-10
**Reviewer**: AI Assistant
**Status**: ❌ REJECT — Wymaga poprawek przed merge

---

## 🚨 BLOCKERS (MUSZĄ BYĆ NAPRAWIONE)

### 1. Brak migracji bazy danych — CRITICAL 🔴

**Plik**: `docker/grading-api/app/db.py` (linia 101)

**Problem**:
```python
case_assigned_at REAL NOT NULL
```

- `NOT NULL` bez wartości domyślnej → **ISTNIEJĄCE BAZY SIĘ ZEPSUJĄ**
- Każda próba `INSERT` do istniejącej tabeli `progress` bez tego pola **zawiedzie**
- Brak pliku migracyjnego, brak `ALTER TABLE` dla istniejących deploymentów
- 💥 **Skutek**: Production outage dla wszystkich obecnych użytkowników

**Fix**:
```sql
-- Dodaj plik migracyjny: docker/grading-api/migrations/001_add_case_assigned_at.sql
ALTER TABLE progress ADD COLUMN case_assigned_at REAL DEFAULT (strftime('%s', 'now'));
UPDATE progress SET case_assigned_at = strftime('%s', 'now') WHERE case_assigned_at IS NULL;
```

**Priorytet**: P0

---

### 2. Race condition w `_advance_progress` — CRITICAL 🔴

**Plik**: `docker/grading-api/app/main.py` (linie 144-147)

**Problem**:
```python
conn.execute(
    "UPDATE progress SET case_order_index = ?, case_assigned_at = ? WHERE student_id = ?",
    (order_index + 1, db.now(), student_id),
)
```

- Brak transakcji wokół `SELECT` + `UPDATE`
- Dwa równoczesne requesty tego samego studenta → **nadpisanie stanu**
- `case_assigned_at` może być niepoprawny przy concurrent requests
- 💥 **Skutek**: Zniekształcone dane czasowe w publikacji naukowej

**Fix**:
```python
def _advance_progress(conn, student_id: str, stage: str, order_index: int):
    with conn:  # transakcja
        # cały SELECT + UPDATE logic
```

**Priorytet**: P0

---

### 3. Test z hard-coded timingiem — CRITICAL 🔴

**Plik**: `docker/grading-api/tests/test_state_machine.py` (linia 374)

**Problem**:
```python
assert 40 <= row["time_spent_seconds"] <= 44  # ~42s
```

- Test zależy od czasu wykonania — **FLAKY TEST**
- Wolne CI/CD → test padnie
- Debugowanie zajmie godziny zanim ktoś zauważy że to timing
- 💥 **Skutek**: False negatives w CI, utrata zaufania do testów

**Fix**:
```python
from unittest.mock import patch

def test_submit_computes_time_spent_seconds_server_side(client, mint_token):
    with patch('db.now', return_value=1000):
        # test logic with deterministic time
```

**Priorytet**: P0

---

## ⚠️ WYSOKI PRIORYTET

### 4. `time_spent_seconds` wciąż w API — HIGH 🟠

**Plik**: `docker/grading-api/app/main.py` (linia 201)

**Problem**:
```python
time_spent_seconds: Optional[float] = None
```

- Pole wciąż istnieje w `SubmitBody`, tylko jest ignorowane
- **Mylące dla developerów** — wygląda jak valid field
- Frontend wciąż może to wysyłać → **false sense of security**
- 💥 **Skutek**: Ktoś w przyszłości może to przypadkiem użyć

**Fix**: **CAŁKOWICIE USUNĄĆ** z `SubmitBody`

**Priorytet**: P1

---

### 5. Brak testu dla `_get_or_create_progress` — HIGH 🟠

**Problem**: Funkcja która ustawia `case_assigned_at` nie ma testu

- Co jeśli student ma już progress z przed migracji?
- Brak testu dla ścieżki "nowy student" vs "istniejący student"
- 💥 **Skutek**: Nie wykryjemy błędów w inicjalizacji czasu

**Fix**: Dodać test:
```python
def test_new_student_gets_case_assigned_at(client, mint_token):
    """Verify case_assigned_at is set on first access for new student."""
    token = mint_token("stu_new")
    case_id = _case_id(client, token)
    
    conn = db_module.get_connection()
    try:
        row = conn.execute(
            "SELECT case_assigned_at FROM progress WHERE student_id = ?",
            ("stu_new",),
        ).fetchone()
    finally:
        conn.close()
    
    assert row["case_assigned_at"] is not None
```

**Priorytet**: P1

---

### 6. `db.now()` zwraca FLOAT — MEDIUM 🟡

**Plik**: `docker/grading-api/app/db.py`

**Problem**:
```python
def now():
    return time.time()  # float z sekundami
```

- Floaty mają ograniczoną precyzję
- Porównania czasowe mogą być niedokładne
- Trudne do debugowania przy sub-second timing

**Fix**: Użyć `datetime` lub przynajmniej dokumentować precyzję

**Priorytet**: P2

---

### 7. Brak indeksu na `case_assigned_at` — MEDIUM 🟡

**Plik**: `docker/grading-api/app/db.py`

**Problem**: Jeśli będziesz chciał queryć po czasie (raporty, analytics) — **full table scan**

**Fix**:
```sql
CREATE INDEX idx_progress_assigned_at ON progress(case_assigned_at);
```

**Priorytet**: P2

---

## 📋 ŚREDNI PRIORYTET

### 8. Komentarze zbyt długie — MEDIUM 🟡

**Plik**: `docker/grading-api/app/main.py` (linie 201-209)

**Problem**: Komentarze są dłuższe niż kod (8 linii)

**Fix**: Przenieść do dokumentacji API, zostawić 1-linijkowy komentarz

**Priorytet**: P3

---

### 9. Brak walidacji `case_assigned_at` — MEDIUM 🟡

**Plik**: `docker/grading-api/app/main.py` (linia 258)

**Problem**:
```python
time_spent_seconds = db.now() - progress["case_assigned_at"]
```

- Co jeśli `case_assigned_at` jest w przyszłości? (zegar serwera)
- Ujemny `time_spent_seconds` trafi do bazy
- Brak sanity check

**Fix**:
```python
time_spent_seconds = max(0, db.now() - progress["case_assigned_at"])
```

**Priorytet**: P2

---

## 📊 OCENA KOŃCOWA

| Kategoria | Ocena | Komentarz |
|-----------|-------|-----------|
| Bezpieczeństwo | ⭐⭐ | Race condition, brak transakcji |
| Migracja danych | ⭐ | **BRAK MIGRACJI** |
| Testy | ⭐⭐ | Flaky test, brak coverage |
| API Design | ⭐⭐ | Martwe pole w modelu |
| Dokumentacja | ⭐⭐⭐⭐ | Dobre komentarze |
| **Średnio** | **1.8/5** |

---

## 🛑 WERDYKT: REJECT

**Wymagane przed merge**:
- [ ] Dodać plik migracyjny dla istniejących baz
- [ ] Dodać transakcje wokół SELECT + UPDATE
- [ ] Naprawić flaky test (użyć mocka czasu)
- [ ] Usunąć `time_spent_seconds` z `SubmitBody`
- [ ] Dodać testy dla `_get_or_create_progress`
- [ ] Dodać walidację ujemnego czasu

**Opcjonalnie**:
- [ ] Dodać indeks na `case_assigned_at`
- [ ] Skrócić komentarze

---

## 📝 CHECKLISTA POPRAWEK

### Pliki do utworzenia:
- [ ] `docker/grading-api/migrations/001_add_case_assigned_at.sql`

### Pliki do modyfikacji:
- [ ] `docker/grading-api/app/db.py` — dodać indeks
- [ ] `docker/grading-api/app/main.py` — transakcje, walidacja, usunąć pole
- [ ] `docker/grading-api/tests/test_state_machine.py` — naprawić test, dodać coverage
