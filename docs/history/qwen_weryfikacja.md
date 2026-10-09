# Weryfikacja ustaleń audytu DoD Tier 4 — master `6dab554`

**Data:** 2026-10-05 · **Repo:** xWuWux/DICOM_app_viewer · **Rewizja:** `6dab554` (tożsama z rewizją audytu)
**Pochodzenie:** weryfikacja wykonana przez asystenta AI (pi) na czystym `git worktree`; wszystkie ustalenia §1 odtwarzalne sekcją 6. Ustalenia oznaczone jako „empiryczne” powtórzono na uruchomionym kodzie i kontenerach — wymagają jeszcze przeglądu przez człowieka, tak jak sam audyt.
**Metoda:** osobny `git worktree` na tej samej rewizji (czysty checkout, bez zmian w bieżącym checkoutie), testy uruchomione lokalnie (Python 3.14, fastapi 0.141.1, pytest 8.3.3), kontenery uruchamiane na Docker Desktop 29.7.2, fakty procesowe z GitHub API (read-only, token lokalnego użytkownika).

Wniosek zbiorczy: **audyt jest w przeważającej większości poprawny**. 100% sprawdzonych wierszy potwierdzonych; stwierdzono **2 ustalenia do sformułowania ponownie**, **1 nieścisłe**, **4 nowe luki** nieobecne w arkuszu oraz **1 fakt, który zmienił się od czasu zapisania plików** (komentarze zostały opublikowane).

---

## 1. Potwierdzone EMPIRYCZNIE (uruchomiony kod / kontener)

| # | Ustalenie | Dowód z uruchomienia |
|---|---|---|
| **P41 / #74** | `validation_error_handler` loguje `exc.errors()` razem z `input` → **pełny token sesji w logu** | `POST /submit` bez `case_id` → 422, a w stdout: `"errors": [{"type":"missing","loc":["body","case_id"],"msg":"Field required","input":{"token":"NBbPluGf0-mzqR7vg4m4fzhpGBCInAawQq9LcwCmuCI","stage":"learning"}}]` (token prawdziwy, wybity przez `POST /session`) |
| **P41 / #74** | przy `text` > 10 000 znaków w logu trafia **cała odpowiedź studenta** | `POST /submit` z tekstem 20 024 zn. → 422; **linia logu 20 365 znaków**, w niej 20 002 znaki treści. Dodatkowo wątek objętościowy: 1 żądanie = ~20 KB logu (patrz §3.4) |
| **P41 (nginx)** | token z query stringa trafia do logów nginx (wcześniej oznaczone „do weryfikacji na uruchomionym kontenerze”) | kontener `dicom_app_viewer-viewer`, `GET /api/case?token=SEKRET-N-QUERY` → `access.log → /dev/stdout`: `172.17.0.1 - - [...] "GET /api/case?token=SEKRET-N-QUERY HTTP/1.1" 502`; **oraz w error.log**: `upstream: "http://127.0.0.1:8000/case?token=SEKRET-N-QUERY"`. Przed tym chroni wyłącznie `--no-access-log` uvicorn, nie nginx |
| **#70** | teza „brak `busy_timeout`” jest nieprecyzyjna; faktycznie: jawny limit nieustawiony, obsługa błędu brak | drugi pisarz: `OperationalError: database is locked` po **5,01 s**; przez API `GET /case` przy trzymanej `BEGIN EXCLUSIVE`: **5,02 s → HTTP 500** `{"error_code":"SERVER_ERROR"}` (nie 503 + `Retry-After`) |
| **P46** | pokrycie 99%, niepokryte `main.py` 263–267 i 346–350 | `pytest -q` → **58 passed**; `--cov=app` → `TOTAL 275 stmts / 4 miss / 99%`, `app/main.py 98% Missing 263-267, 346-350`. **Uzupełnienie:** `pytest-cov` **nie istnieje** w `requirements-dev.txt`, a `scripts/test-grading-api.sh` woła gołe `pytest -q` → w CI pokrycie nie jest mierzone w ogóle (nie tylko „brak bramki”) |
| **P19 / P45 / O4** | scalanie bez recenzji, samodzielenie | PR #83: `reviews: []`, `review_comments: 0`, `requested_reviewers: []`, `author == merged_by == xWuWux`; to samo dla **#9, #10, #11, #12, #13, #14, #15, #17, #18, #58, #59, #75, #76, #81, #82, #84** — wszędzie 0 recenzji |
| **P43 „22 zielone checki”** | liczba 22 potwierdzona | `check-runs` dla SHA `bf191c0` (head PR #83): **22**, wszystkie `success`; na commicie scalającym 11 |
| **P32 / P105 / P110** | brak wydań i tagów → artefakt wdrażany ≠ zweryfikowany | `GET /releases` → `[]`, `GET /tags` → `[]`; w obrazach lokalnych `ipcmc/dicom-viewer-weasis:mvp`, `dicom_app_viewer-viewer:latest` (mutowalne tagi, brak rejestru) |
| **P64** | `#64` działa | `POST :8043/tools/store` → **403** |
| **P23** | walidacja konfiguracji przy starcie | brak `GRADING_COORDINATOR_KEY` → `KeyError: 'GRADING_COORDINATOR_KEY'` przy imporcie (bez komunikatu); `GRADING_TOKEN_TTL_SECONDS="8 godzin"` → `ValueError: invalid literal…`; klucz 1-znakowy → **import przechodzi** (brak sprawdzenia długości) |
| **P17** | `/docs` i `/openapi.json` osiągalne przez proxy | `TestClient`: `/docs` 200, `/openapi.json` 200; map `location /api/ → proxy_pass …:8000/` (potwierdzone logiem upstream: `/api/case` → `http://127.0.0.1:8000/case`), więc `/api/docs` → `/docs` → 200 |
| **#83 zmiana kontraktu 400→409** | faktycznie bez noty | `errors.py` rzuca `AppError(409, …)`; README/CHANGELOG bez wpisu (CHANGELOG nie istnieje) |

---

## 2. Potwierdzone STATYCZNIE (kod/konfiguracja — plik:linia)

**Kod aplikacji**
- **P63** `main.py` `reset()`: `DELETE FROM progress` + `DELETE FROM submissions`, bez archiwum, bez kopii, bez zatwierdzenia; `db.py` `_migrate_existing_schema`: `DROP TABLE submissions_old` bez kopii. `CLAUDE.md:55` „Destructive database operations without explicit confirmation → stop” — brak egzekwacji technicznej. Brak żadnych skryptów backupu w `scripts/` (18 plików, żaden nie robi kopii).
- **P40** `create_session()` i `reset()` **nie emitują żadnego logu**; `audit_log` nie istnieje.
- **P9** `docker/kasm-workspace-weasis/custom_startup.sh:61` → `curl … || CASE_JSON='{"complete": true}'` — cichy fallback, identycznie jak w audycie. Ten sam wiersz wysyła token w URL do `curl`, więc token jest **widoczny w argv procesu**.
- **P61/P62** `_migrate_existing_schema`: bez tabeli wersji, bez Alembica, `executescript` (DDL nieatomowy), idempotencja tylko przez inspekcję `PRAGMA`.
- **P16** martwy kod: `watermark.html:177,198,246,251` i `grading-panel.html:167,188,224,229` — `caseStartMs` / `time_spent_seconds` liczone i wysyłane mimo że serwer ich nie używa.
- **P36** `SessionBody.student_id/session_id: str` bez `max_length`/wzorca; `stage`/`category` tylko `max_length`, bez enum; `err.message` wstawiany przez `innerHTML` (`watermark.html:213,291,346,350`; `grading-panel.html:191,269,315,319`).
- **P64** schemat `db.py`: są `UNIQUE`/FK, **brak `CHECK`** na `stage`/`category`.
- **P71** `scripts/create-session.py`: token bity **przed** `request_kasm` (linia 86 vs 115), bez kompensacji → osierocony token przy awarii Kasm.
- **P81** `logging_config.py`: brak pól `service`/`environment`/`component`; `timestamp` przez `time.strftime("%…%S")` → dokładność do sekundy (w logach widać `2026-10-05T09:44:27Z`).
- **P66** `results()` łączy `submissions.is_correct` (migawka) z **bieżącym** `cases.ground_truth_category` — wzmocnione, patrz §3.2.

**Konfiguracja, obrazy, CI**
- **P113/P38/#51** `docker/grading-api/Dockerfile` i `docker/viewer/Dockerfile`: **bez `USER`** → root. (`kasm-workspace-weasis` ma `USER kasm-user`, `guacamole-weasis` `USER student` — jak w audycie.)
- **P76/P72/P13** w trzech plikach compose **zero** wystąpień: `cap_drop`, `read_only`, `no-new-privileges`, `security_opt`, `mem_limit`, `cpus`, `pids_limit`, `healthcheck`, `logging/max-size`.
- **P60/#69** `HEALTHCHECK` nie ma żaden Dockerfile; w compose brak `healthcheck:`; `depends_on` bez `condition:` (3 pliki).
- **P31/#50** `orthancteam/orthanc:latest` w `docker-compose.yml:4` i `docker-compose.remote-host.yml:35`; Weasis `.deb` przez `curl -fsSL` bez `sha256sum -c`/podpisu (`kasm-workspace-weasis/Dockerfile:23-24`); rozjazd wersji Kasm: `chrome:1.16.0` vs `core-ubuntu-noble:1.19.0` (komentarz w Dockerfile: „pinned to match the actual installed Kasm server version”).
- **P27** `requirements.txt` tylko 2 piny (`fastapi==0.141.1`, `uvicorn==0.53.0`); brak pliku lock/constraints; `grading-api/Dockerfile:8` `apt-get upgrade -y` (nieodtwarzalne).
- **O13/#67** `ci.yml`: brak bloku `permissions:`, brak `timeout-minutes`, akcje po tagach (`checkout@v4`, `setup-python@v5`, `upload-artifact@v4`), hadolint/trivy pobierane `curl -sL` bez sumy kontrolnej, `bats` z `apt-get` bez wersji.
- **P18/P20/P30/P43/#71** `scripts/security-scan.sh`: `bandit -r docker/grading-api/app` (pomija `scripts/*.py`, `overlay.py`, `grading_panel_window.py`), `trivy fs --scanners vuln` (bez licencji), `trivy image` **tylko** `dicom_app_viewer-grading-api` i `-viewer` (2 z 5), `--ignore-unfixed` w obu.
- **P44/O23/P45/O4/P1** w `.github/` istnieje **tylko** `workflows/ci.yml` → brak CODEOWNERS, brak dependabot.yml, brak gitleaks/sekret-skanu, brak ISSUE_TEMPLATE.
- **P73** `default.conf.template`: `limit_req_zone` 10 r/s + `burst=20 nodelay` **tylko** na `/api/`; serwer `:8043` bez limitu; brak `proxy_*_timeout`; brak `proxy_set_header X-Request-Id` (potwierdza P78).
- **P35** `docker-compose.yml:62` `8080:8080` (wszystkie interfejsy) bez powiązania z `BIND_ADDR`; domyślny `guacadmin/guacadmin` w `docker/guacamole/postgres-init/001-initdb.sql:757-765`.
- **P39** TLS wyłącznie opisane (`docs/PROXMOX_DEPLOYMENT.md` §5 certbot + WireGuard), nigdzie niewdrożone; **brak jakiejkolwiek wzmianki** o szyfrowaniu w spoczynku (grep `LUKS|ZFS|szyfrow` w docs → pusto).
- **P37** `.gitignore:20-22` potwierdza: transkrypty z/export zawierały **Kasm API key/secret**; `:54-56` publiczne ~2 tygodnie, wymazane `git-filter-repo` + force-push 2026-09-22. `--insecure` + `ssl._create_unverified_context()` w `create-session.py:51`.

**Dokumentacja (P10, P29, P75, P22, O5/O11, O31, P1, P7)**
- Nie istnieją: `LICENSE`, `CHANGELOG.md`, `docs/observability.md`, `docs/DEGRADED_MODES.md`, `docs/NFR.md`, `docs/CAPACITY.md`, `docs/adr/`, `docs/runbooks/`, `SERVICE.md`.
- `.env.example` dokumentuje **3** zmienne; w kodzie/compose używanych jest **10** (`GRADING_TOKEN_TTL_SECONDS`, `GRADING_DB_PATH`, `BIND_ADDR`, `GRADING_API_URL`, `KASM_SERVER`, `KASM_API_KEY`, `KASM_API_KEY_SECRET`, `KASM_IMAGE_ID`, `GRADING_TOKEN`, `ORTHANC_PASSWORD`).
- `README.md` = **1050 linii** (dokładnie jak w arkuszu), brak sekcji Troubleshooting i tabeli konfiguracji; `#8` opisany w 303 znakach bez kryteriów akceptacji.
- O pojemności tylko komentarze: `db.py:91-93` (50-user pilot / 300-user cohort), `README.md:215` (300–500), `CLAUDE.md:14` (limit Kasm CE 5 sesji, EULA §2.2) — brak dokumentu NFR.
- Tier 4 nie zapisany w `CLAUDE.md`/`README.md`/`docs/` (grep „tier” → pusto) — ale patrz §4.

---

## 3. NOWE ustalenia — nie ma ich w arkuszu ani w szkicach komentarzy

### 3.1 (Wysoka) `request_id` ginie dokładnie przy nieobsłużonym błędzie — łamie P78/P103
`RequestIdMiddleware.dispatch` ma `finally: request_id_var.reset(token)`, a `unhandled_exception_handler` loguje **po** resecie. Zarejestrowana linia:
```json
{"level":"ERROR","logger":"app.errors","message":"unhandled_exception","request_id":"-", "path":"/case", ...}
```
Czyli jedyny błąd, przy którym korelacja jest najbardziej potrzebna, nie ma korelacji. Dla `AppError` i walidacji jest OK (handler działa wewnątrz kontekstu).
**Ryzyko/naprawa:** logować w `dispatch` (przed `finally`) albo resetować dopiero po wysłaniu odpowiedzi; test regresji: wymuś 500 i asertuj `request_id != "-"`. **Wiersz:** P78, P83, P103 (status „Częściowo” → dopisać tę przyczynę).

### 3.2 (Wysoka, wzmocnienie P66) `/results` po zmianie ground truth daje **rekord wewnętrznie sprzeczny**
Scenariusz uruchomiony: uczeń oddaje w `test` kategorię `4A`, `is_correct=1`, `/results` → `accuracy 1.0`, breakdown `{gt:4A, sub:4A, correct:true}`. Po `UPDATE cases SET ground_truth_category='4B'`:
```json
{"accuracy": 1.0, "breakdown": [{"ground_truth": "4B", "submitted": "4A", "correct": true}]}
```
To nie tylko „zmieniony wyświetlany wynik” — to **publikowany rekord, w którym widać błąd, a licznik mówi że dobrze**. Dla danych idących do publikacji naukowej jest to błąd krytyczny, nie „Częściowo/Średnia”.
**Postulat:** zamykać migawkę `ground_truth_category` w `submissions` w chwili `/submit`; ewentualnie wersjonować `cases` i pokazywać wersję z czasu odpowiedzi. **Wiersz:** P66 (proponowana waga: **Krytyczna**), powiązane P8/O17 (retencja/backup).

### 3.3 (Krytyczna, wzmocnienie P23/P35) Pusty `GRADING_COORDINATOR_KEY` uruchamia aplikację
`GRADING_COORDINATOR_KEY=""` → **import przechodzi** (brak jakiejkolwiek walidacji wartości). `os.environ["…"]` zwraca `""`, `compare_digest` ze stringiem pustym → ktokolwiek zna pusty klucz — wybija tokeny. Dziś broni tego **tylko** `${ORTHANC_PASSWORD:?}`-styl w compose; bezpośrednie `docker run`, CI, testy czy zmiana compose na `${VAR:-}` omijają bramkę.
**Naprawa:** przy starcie `if len(COORDINATOR_KEY) < 32: raise SystemExit(czytelny komunikat)` + test. **Wiersz:** P23 (proponowana waga Średnia → **Wysoka**), P37.

### 3.4 (Średnia, nowe) Handler 422 tworzy **nieograniczony wzrost logu**
Przy `max_length=10_000` sama odmowa produkuje ~20 KB logu na żądanie, a `limit_req` nginx (10 r/s burst 20) pozwala na ~30 takich żądań w secie burst. Łączy się z P72 (nieograniczony wzrost) i #66 (log bez rotacji) — a `--no-access-log`/json-file bez `max-size` nic tu nie ogranicza.
**Naprawa:** odrzucać `input` (patrz P41) + obcinać długość `msg`; limit objętości ładunku na nginx (`client_max_body_size`).

### 3.5 (Dodatkowy dowód, nie nowa luka) Token w `error.log` nginx
Oprócz `access.log` (udokumentowane w arkuszu) **`error.log` zawiera pełny upstream URL z tokenem** (`upstream: "http://127.0.0.1:8000/case?token=…"`) — czyli wyciek przeżywa nawet po wyłączeniu access logu. Dodać do P41/#63 („unieważnić tokeny, jeśli logi były gdziekolwiek zbierane” dotyczy też error.log).

### 3.6 (Informacja) `docker-compose.remote-host.yml` publikuje `:8043` na `0.0.0.0`
`remote-host.yml:73-74` → `${BIND_ADDR:-0.0.0.0}:8080` **oraz `:8043`**, podczas gdy `docker-compose.yml:65` przypina 8043 do `127.0.0.1`. Plik sam o tym pisze i wymaga ręcznego firewalla — czyli kontrola jest **deklarowana, nie wdrażana** (P25/O15). W arkuszu P38/P35 nie ma tego akcentu.

---

## 4. Co się zmieniło od czasu zapisania plików (stan na dziś)

**Nagłówek `Komentarze_do_zgłoszeń_DoD_audyt.md` („szkice, nic nie opublikowano”) jest nieaktualny.**
- Komentarze audytu są **opublikowane** (aut. `lukaszkosminski`) na: **#4, #27, #28, #29, #30, #43, #50, #51, #54, #60, #61, #63, #65, #66, #67, #68, #69, #70, #71, #72, #73, #74, #8** — po 1 komentarzu na zgłoszenie (#72, #74, #8 miały wcześniej inne komentarze).
- Założone zgłoszenia **#85–#91** z etykietami `dod-audit` + `tier: 4` (09:21–09:25Z): #85 backup/restore/DR (Krytyczna), #86 SERVICE.md + deklaracja Tieru, #87 klasyfikacja danych/retencja/RODO, #88 runbooki + postmortem, #89 NFR/SLI/SLO, #90 proces/ADR/szablon, #91 konfiguracja jako kod + wykrywanie dryfu.
  → **Wiersz O33** („Tier 4 nie zapisany w repozytorium”) wymaga statusu **Częściowo**: etykieta `tier: 4` istnieje w trackerie i #86 dokumentuje w `SERVICE.md`, nadal brak tego w `CLAUDE.md`.
- Stan liczników: **19 otwartych** issue (najwyższy numer 91), **zero z przypisanym** → O6 pozostaje prawdziwe.
- `#8` nadal otwarty, bez przypisania, 303 znaki opisu, bez kryteriów akceptacji → P1 bez zmian.

---

## 5. USTALENIA DO POPRAWKI (obalone / nieścisłe)

| Wiersz | Twierdzenie w arkuszu | Stan faktyczny | Co wpisać |
|---|---|---|---|
| **P115 / P21** | „ARCHITECTURE.md bez przepływu Weasis” | **OBALONE** — `docs/ARCHITECTURE.md` ma **12** wzmianek Weasis, w tym sekcja od linii 126 opisująca druga ścieżkę sesji (`docker/kasm-workspace-weasis/`, autostart Weasis, panel w osobnym oknie) | Zastąpić: „ARCHITECTURE.md opisuje przepływ Weasis **jako ‘being built alongside’**, mimo że ta ścieżka jest już wdrożona (`README.md:41` Status: Done) → problem nie w treści, lecz w statusie/aktualności” |
| **P17** | „FastAPI generuje OpenAPI, ale **bez wersji**” | **NIEŚCISŁE** — `openapi.json` ma `info.version = "0.1.0"` (domyślne FastAPI, niczym niesterowane) | Zastąpić: „wersja schematu to domyślne `0.1.0`, nic jej nie aktualizuje i nie jest związana z rewizją → kontrakt faktycznie nie jest wersjonowany (brak `/v1`)” |
| **P115 / O31 / P21 (dryf)** | „README 21 vs 58 testów; 14 vs 16 wizualnych” | **Częściowo niekompletne** — trzeci suchy: `README.md:891` „BATS tests (20 tests total)”, realnie **28** `@test` (`launch-session.bats` 8 + `custom_startup.bats` 9 + `watchdog.bats` 4 + `custom_startup.bats` 7). Wizualne: 14 snapshottów (7 stanów × 2 strony) + test `case_mismatch_409` z #83 → 16 collectów | Dopisać suchy liczby BATS do wiersza P115/O31 |
| **P41/#63** | „nginx … (do weryfikacji na uruchomionym kontenerze)” | **Zweryfikowane na żywym kontenerze** (patrz §1) — usunąć zastrzeżenie, podnieść status z „hipoteza” na „potwierdzone empirycznie” | przenieść do „Zweryfikowane powtórzone na kopii repozytorium” |
| **#70** | teza zgłoszenia „brak `busy_timeout`” | audyt miał rację że teza jest nieprecyzyjna; **teraz zmierzone**: 5,01 s / 5,02 s → **500** | wpisać liczby (5,01 s / 5,02 s / HTTP 500) jako dowód |
| **P46** | „brak automatycznej bramki pokrycia” | mocniejsze: pokrycia **nie ma w CI wcale** (brak `pytest-cov` w `requirements-dev.txt`, `test-grading-api.sh` = `pytest -q`) | dopisać przyczynę |
| **P19 (próbka)** | „Próbka PR #9–#18 bez formalnych akceptacji” | **#16 nie jest PR-em** (404). Poprawna próbka: #9–#15, #17, #18 (+ #58, #59, #75, #76, #81, #82, #84) — wszystkie 0 recenzji | skorygować numerację |
| **O33** | „Tier 4 nie zapisany w repozytorium” | nieaktualne w pełni — etykieta `tier: 4` w trackerze + #86 | status → **Częściowo (w toku, #86)** |
| **Ochrona brancha (P19/P45/O4)** | „master jest chroniony, ale bez wymogu recenzji” | **NIEZWERYFIKOWANE** — `GET /branches/master/protection` → 404 przy tokenie bez uprawnienia `admin` (konto `lukaszkosminski`: `push: true, admin: false`). Nie da się rozstrzygnąć z API bez admina | sprawdzić w UI (`Settings → Branches`) albo poprosić `xWuWux`; na razie wpisać „do weryfikacji w UI” |

---

## 6. Jak odtworzyć

```bash
# czyste środowisko audytu (nie rusza bieżącego checkoutu)
WT=$(mktemp -d)/audit-verify
git -C <twój-klon> worktree add "$WT" 6dab554
python3 -m venv /tmp/venv-audit
/tmp/venv-audit/bin/pip install -r "$WT/docker/grading-api/requirements-dev.txt" pytest-cov

# P46 — 58 testów, pokrycie 99%, braki main.py 263-267 i 346-350
cd "$WT/docker/grading-api"
GRADING_COORDINATOR_KEY=$(python3 -c "print('k'*48)") /tmp/venv-audit/bin/python -m pytest -q --cov=app --cov-report=term-missing

# P41 / #74 (token + odpowiedź studenta w logu 422)
GRADING_DB_PATH=/tmp/p41.db /tmp/venv-audit/bin/python repro_p41.py

# #70 (5,01 s -> database is locked -> HTTP 500)
GRADING_DB_PATH=/tmp/p70.db /tmp/venv-audit/bin/python repro_p70.py

# P66 (dryf ground truth w /results), P23 (walidacja konfiguracji), P17 (/docs)
GRADING_DB_PATH=/tmp/p66.db /tmp/venv-audit/bin/python repro_p66.py

# P41 w nginx + sprawdzenie, że #64 wciąż działa
docker run -d --rm --name ngt -p 18080:8080 -p 18043:8043 \
  --add-host grading-api:127.0.0.1 --add-host orthanc:127.0.0.1 \
  -e ORTHANC_USER=ocr -e ORTHANC_PASSWORD=testpass dicom_app_viewer-viewer:latest
curl 'http://127.0.0.1:18080/api/case?token=SEKRET-N-QUERY'   # 502, ale token w logach
docker logs ngt | grep token                                  # access.log ORAZ error.log
curl -X POST http://127.0.0.1:18043/tools/store               # 403 -> #64 OK
```

Skrypty `repro_p41.py`, `repro_p70.py`, `repro_p66.py` to około 40-liniowe skrypty na `fastapi.testclient` z §1 (nie są częścią tego commitu — dodać do `scripts/` tylko wtedy, jeśli ustalenia mają trafić do regression testów; zgodnie z §3.1 i §3.3 właśnie takich testów brakuje).

Po zakończeniu weryfikacji: `git -C <twój-klon> worktree remove "$WT"`.
