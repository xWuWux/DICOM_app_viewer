#!/bin/sh
# #103: migrate only the official stock account; verify customized accounts.
# SHA-256(password + uppercase hex salt) is Guacamole JDBC's native format.
# Passwords never enter SQL, argv or diagnostics. Environment transport remains
# visible to Docker administrators (#216); this is not the file-secret migration.
set +x
set -eu
fail() { echo "Guacamole admin bootstrap failed: $1" >&2; exit 1; }
password=${GUACAMOLE_ADMIN_PASSWORD:-}
unset GUACAMOLE_ADMIN_PASSWORD
[ "${#password}" -ge 32 ] || fail 'password must contain at least 32 characters'
[ "${#password}" -le 1024 ] || fail 'password exceeds 1024 characters'
# Restrict bootstrap input to printable ASCII without edge spaces. This avoids
# shell/Compose encoding ambiguity; generate with openssl rand -hex 32.
LC_ALL=C
export LC_ALL
case "$password" in *[!\ -~]*|\ *|*\ ) fail 'password must be printable ASCII without edge spaces';; esac

row=$(psql -X -Atq -v ON_ERROR_STOP=1 2>/dev/null <<'SQL'
SELECT upper(encode(u.password_salt, 'hex')) || '|' ||
       upper(encode(u.password_hash, 'hex')) || '|' || u.disabled::text
FROM guacamole_user u JOIN guacamole_entity e USING (entity_id)
WHERE e.name = 'guacadmin' AND e.type = 'USER';
SQL
) || fail 'database read failed'
salt=${row%%|*}
rest=${row#*|}
hash=${rest%%|*}
disabled=${rest#*|}
case "$salt$hash" in *[!0-9A-F]*) fail 'unexpected account record';; esac
[ "${#salt}" -eq 64 ] && [ "${#hash}" -eq 64 ] || fail 'missing or unsupported account record'
case "$disabled" in true|false) ;; *) fail 'unexpected account status';; esac
expected=$(printf '%s%s' "$password" "$salt" | sha256sum)
expected=$(printf '%s' "${expected%% *}" | tr 'a-f' 'A-F')
[ "${#expected}" -eq 64 ] || fail 'password hashing failed'
stock_salt=FE24ADC5E11E2B25288D1704ABE67A79E342ECC26064CE69C5B3177795A82264
stock_hash=CA458A7D494E3BE824F5E1E175A1556C0F8EEF2C2D7DF3633BEC4A29C4411960
if [ "$salt" != "$stock_salt" ] || [ "$hash" != "$stock_hash" ]; then
  [ "$expected" = "$hash" ] && [ "$disabled" = false ] || fail 'custom account mismatch or disabled; restore the configured credential, do not reset the database'
  echo 'Guacamole administrator verified (unchanged).'
  exit 0
fi
new_salt=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n' | tr 'a-f' 'A-F')
[ "${#new_salt}" -eq 64 ] || fail 'salt generation failed'
new_hash=$(printf '%s%s' "$password" "$new_salt" | sha256sum)
new_hash=${new_hash%% *}
unset password
case "$new_salt$new_hash" in *[!0-9a-fA-F]*) fail 'invalid hash material';; esac
[ "${#new_hash}" -eq 64 ] || fail 'password hashing failed'
# Compare-and-update: concurrent credential changes cannot be overwritten.
changed=$(psql -X -Atq -v ON_ERROR_STOP=1 2>/dev/null <<SQL
WITH changed AS (
  UPDATE guacamole_user u SET password_salt=decode('$new_salt','hex'),
    password_hash=decode('$new_hash','hex'), password_date=CURRENT_TIMESTAMP,
    disabled=false
  FROM guacamole_entity e
  WHERE u.entity_id=e.entity_id AND e.name='guacadmin' AND e.type='USER'
    AND u.password_salt=decode('$stock_salt','hex')
    AND u.password_hash=decode('$stock_hash','hex')
  RETURNING u.user_id
) SELECT count(*) FROM changed;
SQL
) || fail 'database update failed'
[ "$changed" = 1 ] || fail 'account changed concurrently; retry verification'
echo 'Guacamole stock administrator migrated.'
