#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; N='\033[0m'
fail() { echo -e "${R}✗ $1${N}"; exit 1; }
ok()   { echo -e "${G}✓ $1${N}"; }
step() { echo -e "${Y}→ $1${N}"; }

step "1/5 Проверяю незакоммиченные правки"
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo; git status --short; echo
    fail "На сервере есть правки. Закоммить (git add . && git commit -m '...') или откатить (git checkout -- ФАЙЛ)."
fi
ok "Рабочее дерево чистое"

step "2/5 Тяну изменения с GitHub"
git fetch origin -q
BEFORE=$(git rev-parse HEAD)
git pull --rebase || fail "git pull упал — разбери сообщение выше."
AFTER=$(git rev-parse HEAD)
if [ "$BEFORE" = "$AFTER" ]; then
    ok "Новых коммитов нет"
else
    ok "Подтянуто коммитов: $(git log --oneline "$BEFORE..$AFTER" | wc -l)"
    git log --oneline "$BEFORE..$AFTER" | sed 's/^/      /'
fi
UNPUSHED=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l)
[ "$UNPUSHED" -gt 0 ] && echo -e "${Y}      ⚠ $UNPUSHED незапушенных коммит(ов) на сервере — сделай git push${N}"

step "3/5 Пересобираю контейнер"
docker compose pull >/dev/null || fail "Не удалось скачать образ из GHCR."
docker compose up -d >/dev/null || fail "Контейнер не стартовал."
ok "Контейнер запущен"

step "4/5 Жду готовности бэкенда"
for i in $(seq 1 20); do
    curl -sf http://localhost:8000/health >/dev/null 2>&1 && { ok "Бэкенд отвечает"; break; }
    [ "$i" -eq 20 ] && fail "Не поднялся за 20 сек. Смотри: docker compose logs backend"
    sleep 1
done

step "5/5 Сверяю код в контейнере с диском"
DISK=$(cd backend && find app -name '*.py' | LC_ALL=C sort | xargs md5sum | md5sum | cut -d' ' -f1)
CONT=$(docker compose exec -T backend sh -c 'find app -name "*.py" | LC_ALL=C sort | xargs md5sum | md5sum' | cut -d' ' -f1)
[ "$DISK" = "$CONT" ] || fail "Код в контейнере УСТАРЕЛ. Попробуй: docker compose build --no-cache && docker compose up -d"
ok "Код в контейнере актуален"

echo
echo -e "${G}Готово. Теперь Ctrl+Shift+R в браузере.${N}"
