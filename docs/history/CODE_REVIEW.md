# Code Review — DICOM Viewer MVP

**Data przeglądu**: 2026-09-10  
**Przeglądane przez**: lukaszkosminski  
**Wersja**: MVP (commit bieżący)

---

## 📋 Podsumowanie

To **solidny MVP** aplikacji DICOM viewer z watermarks i systemem oceniania Lung-RADS. Projekt jest dobrze udokumentowany, z jasno określonymi zasadami bezpieczeństwa i architekturą.

### Ogólna ocena: 3.5/5 ⭐

| Kategoria | Ocena | Komentarz |
|-----------|-------|-----------|
| **Architektura** | ⭐⭐⭐⭐ | Dobra izolacja, ale brakuje Postgres |
| **Bezpieczeństwo** | ⭐⭐⭐⭐ | Solidny watermark, ale brakuje rate limiting |
| **Kod** | ⭐⭐⭐ | Czytelny, ale brakuje walidacji i obsługi błędów |
| **Testy** | ⭐⭐ | Tylko smoke testy, brak unit/E2E |
| **Dokumentacja** | ⭐⭐⭐⭐⭐ | Wzorcowa |
| **MVP Scope** | ⭐⭐⭐ | Zawężony, ale świadomy wybór |

---

## ✅ Mocne strony

### 1. **Bezpieczeństwo i architektura**
- ✅ **Izolacja sieciowa**: Orthanc bez publicznego portu, dostęp tylko przez `ipcmc-internal`
- ✅ **Forensic watermark**: Tiled overlay z `mix-blend-mode:difference` gwarantuje kontrast
- ✅ **Anti-tamper**: MutationObserver przeładowuje stronę jeśli watermark jest ukryty
- ✅ **Auth injection**: Nginx wstrzykuje Basic Auth serwer-side, nie w iframe
- ✅ **Ephemeral containers**: Kasm niszczy kontenery po wylogowaniu

### 2. **Dokumentacja**
- ✅ **CLAUDE.md**: Jasne zasady ("Hard Rules"), których nie można łamać
- ✅ **README.md**: Co jest zaimplementowane, co odroczone
- ✅ **ARCHITECTURE.md**: Diagramy Mermaid pokazujące flow sesji i topologię
- ✅ **Komentarze w kodzie**: Szczegółowe wyjaśnienia decyzji projektowych

### 3. **Testy i CI**
- ✅ **lint.sh**: Sprawdzenie składni bash i walidacja compose
- ✅ **smoke-test.sh**: Testy endpointów HTTP z retry logic
- ✅ **CI workflow**: Uruchamia oba skrypty na każdym push/PR

### 4. **Grading API**
- ✅ **3-stage state machine**: Nauka → Ocena → Test
- ✅ **Enforce ground truth secrecy**: Ground truth nigdy nie jest zwracane przed właściwym stage
- ✅ **SQLite**: Lżejsze niż Postgres dla MVP, łatwa migracja później

---

## ⚠️ Problemy krytyczne (P0)

### 1. **Brak stratified sampling** (CLAUDE.md Hard Rule #4)

**Plik**: `docker/grading-api/app/db.py`

```python
# Obecnie:
SEED_CASES = [
    ("learning", 0, "UID1", "Przypadek 1", "2", 0, "Report..."),
    ("assessment", 0, "UID2", "Przypadek 1", "3", 0, "Report..."),
    ("test", 0, "UID3", "Przypadek 1", "4A", 1, "Report..."),
]
```

**Problem**: Jest 1 przypadek na stage zamiast puli 500 studiów z rozkładem 50/50/30.

**Ryzyko**: Fundamentalna funkcjonalność z CLAUDE.md nie jest zaimplementowana.

**Rekomendacja**:
```python
# Dodaj tabelę do stratified sampling
CREATE TABLE sampling_config (
    stage TEXT NOT NULL,
    lung_rads_category TEXT NOT NULL,
    count INTEGER NOT NULL,
    UNIQUE(stage, lung_rads_category)
);

# Algorytm losowania
def get_stratified_sample(conn, student_id, stage):
    # Losuj 50% z kategorii 1-2, 50% z 3-4X
    ...
```

**Priorytet**: P0 — Krytyczne

---

### 2. **Brak prawdziwej walidacji medycznej**

**Plik**: `docker/grading-api/app/main.py`

```python
is_correct = 1 if body.category == case["ground_truth_category"] else 0
```

**Problem**: Ground truth jest hardcoded w bazie, bez walidacji przez radiologa.

**Ryzyko**: Nie można użyć do rzeczywistego testowania bez zatwierdzenia eksperta.

**Rekomendacja**:
```python
# Dodaj tabelę review
CREATE TABLE case_reviews (
    case_id INTEGER,
    reviewer_id TEXT,
    reviewed_at TIMESTAMP,
    approved BOOLEAN,
    comments TEXT
);

# Dodaj endpoint do review
@app.post("/cases/{case_id}/review")
def review_case(case_id: int, reviewer: ReviewerBody):
    ...
```

**Priorytet**: P0 — Krytyczne

---

### 3. **Placeholder content w seed data**

**Plik**: `docker/grading-api/app/db.py`

```python
"PLACEHOLDER — nie jest to prawdziwy opis kliniczny."
```

**Problem**: Sample data (CT_small, MR_small, BRAINIX) to nie są badania płuc.

**Ryzyko**: Nie można użyć do testowania medycznego.

**Rekomendacja**: Dodać pipeline anonimozacji DICOM i curację prawdziwych przypadków.

**Priorytet**: P0 — Krytyczne

---

## 🔧 Problemy techniczne (P1-P2)

### 1. **Brak indeksów w SQLite**

**Plik**: `docker/grading-api/app/db.py`

```python
# Obecnie:
CREATE TABLE submissions (
    student_id TEXT NOT NULL,
    case_id INTEGER NOT NULL,
    ...
);
```

**Problem**: Brak indeksów na `student_id`, `stage`, `submitted_at`.

**Rekomendacja**:
```python
conn.execute("CREATE INDEX idx_submissions_student ON submissions(student_id)")
conn.execute("CREATE INDEX idx_submissions_stage ON submissions(stage)")
conn.execute("CREATE INDEX idx_submissions_submitted ON submissions(submitted_at)")
```

**Priorytet**: P1 — Wysoki

---

### 2. **Brak walidacji czasu w grading-api**

**Plik**: `docker/grading-api/app/main.py`

```python
time_spent_seconds: Optional[float] = None
```

**Problem**: Brak walidacji (np. ujemny czas, absurdalnie długi).

**Rekomendacja**:
```python
if body.time_spent_seconds is not None:
    if body.time_spent_seconds < 0 or body.time_spent_seconds > 7200:
        raise HTTPException(400, "Invalid time_spent_seconds")
```

**Priorytet**: P1 — Wysoki

---

### 3. **Brak obsługi błędów w frontend**

**Plik**: `docker/viewer/watermark.html`

```javascript
const res = await fetch(`/api/case?student_id=${studentId}`);
const data = await res.json();
// brak obsługi błędów sieciowych
```

**Rekomendacja**:
```javascript
try {
    const res = await fetch(`/api/case?student_id=${studentId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    ...
} catch (err) {
    panel.innerHTML = `<p class="error">Błąd ładowania: ${err.message}</p>`;
}
```

**Priorytet**: P1 — Wysoki

---

### 4. **Nginx: Brak rate limiting**

**Plik**: `docker/viewer/default.conf.template`

```nginx
location /api/ {
    proxy_pass http://grading-api:8000/;
    # brak rate limiting
}
```

**Rekomendacja**:
```nginx
# Dodaj na poziomie http
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

# W location /api/
limit_req zone=api_limit burst=20 nodelay;
limit_req_status 429;
```

**Priorytet**: P2 — Średni

---

### 5. **Brak backup strategy dla Orthanc**

**Plik**: `docker/orthanc/orthanc.json`

```json
"StorageDirectory": "/var/lib/orthanc/db"
```

**Rekomendacja**:
```yaml
# docker-compose.yml
volumes:
  - orthanc-backup:/backups
  - ./scripts/backup-orthanc.sh:/usr/local/bin/backup.sh
```

```bash
# scripts/backup-orthanc.sh
#!/bin/bash
tar -czf /backups/orthanc-$(date +%Y%m%d).tar.gz /var/lib/orthanc/db
```

**Priorytet**: P2 — Średni

---

### 6. **Brak testów jednostkowych**

**Plik**: `.github/workflows/ci.yml`

```yaml
# Obecnie tylko:
- run: ./scripts/lint.sh
- run: ./scripts/smoke-test.sh
```

**Rekomendacja**:
```yaml
- name: Unit tests
  run: |
    docker exec ipcmc-grading-api pytest /srv/app/tests/
```

**Priorytet**: P2 — Średni

---

## 📋 Drobne uwagi (P3)

### 1. **Hardcoded wartości**
```python
# watermark.html
const CELL_W = 900, CELL_H = 420;
```
**Rekomendacja**: Przenieść do config lub CSS variables.

---

### 2. **Brak health check z DB connection**
```python
@app.get("/healthz")
def healthz():
    return {"status": "ok"}
    # Brak sprawdzenia połączenia z bazą
```
**Rekomendacja**:
```python
@app.get("/healthz")
def healthz():
    try:
        conn = db.get_connection()
        conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(503, f"DB error: {e}")
```

---

### 3. **Watermark opacity**
```css
opacity: 0.3;  # może być za ciemny
```
**Rekomendacja**: Dodać jako CSS variable do konfiguracji.

---

### 4. **Brak obsługi 404 w Orthanc**
```javascript
frame.src = `${orthancUrl}ui/app/index.html#/filtered-studies?...`
// brak obsługi przypadku gdy UID nie istnieje
```

---

## 🎯 Plan działania

### Faza 1 (P0) — Krytyczne
- [ ] Dodać stratified sampling z Postgres
- [ ] Pipeline anonimozacji DICOM
- [ ] Walidacja medyczna przez eksperta

### Faza 2 (P1) — Wysokie
- [ ] Indeksy SQLite
- [ ] Walidacja czasu w grading-api
- [ ] Obsługa błędów w frontend

### Faza 3 (P2) — Średnie
- [ ] Rate limiting w nginx
- [ ] Backup strategy dla Orthanc
- [ ] Testy jednostkowe API

### Faza 4 (P3) — Niskie
- [ ] CSS variables dla watermark
- [ ] Walidacja environment variables
- [ ] Canvas zamiast DOM dla watermark

---

## 📊 Metryki jakości kodu

| Metryka | Wartość | Cel |
|---------|---------|-----|
| Coverage testów | ~10% | >80% |
| Liczba komentarzy | Wysoka | Dobra |
| Złożoność funkcji | Niska | Dobra |
| Technical debt | Średni | Do redukcji |

---

## 🔗 Powiązane dokumenty

- [CLAUDE.md](./CLAUDE.md) — Hard Rules projektu
- [README.md](./README.md) — Co jest zaimplementowane
- [ARCHITECTURE.md](./docs/ARCHITECTURE.md) — Diagramy architektury
- [Dokumentacja/](./Dokumentacja/) — Wymagania medyczne

---

## ✍️ Podpis

**Reviewer**: lukaszkosminski  
**Data**: 2026-09-10  
**Status**: ✅ Zatwierdzono z uwagami
