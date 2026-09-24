#!/usr/bin/env bash
cd "$(dirname "$0")"
G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; N='\033[0m'
y() { echo -e "  ${G}✓${N} $1"; }
n() { echo -e "  ${R}✗${N} $1"; }
w() { echo -e "  ${Y}?${N} $1"; }

echo; echo "═══ IT-Deck — состояние системы ═══"; echo

echo "GIT"
git fetch origin -q 2>/dev/null
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
    && y "Синхронизирован с GitHub" || n "РАСХОЖДЕНИЕ с GitHub — нужен ./deploy.sh или git push"
git diff --quiet && git diff --cached --quiet \
    && y "Нет локальных правок" || n "Есть незакоммиченные правки на сервере"
echo "      HEAD: $(git log -1 --oneline)"

echo; echo "КОНТЕЙНЕР"
if [ -n "$(docker compose ps -q backend 2>/dev/null)" ] && docker compose exec -T backend true 2>/dev/null; then
    y "Запущен"
    DISK=$(cd backend && find app -name '*.py' | LC_ALL=C sort | xargs md5sum | md5sum | cut -d' ' -f1)
    CONT=$(docker compose exec -T backend sh -c 'find app -name "*.py" | LC_ALL=C sort | xargs md5sum | md5sum' 2>/dev/null | cut -d' ' -f1)
    [ "$DISK" = "$CONT" ] && y "Код внутри совпадает с диском" || n "Код внутри УСТАРЕЛ — запусти ./deploy.sh"
else
    n "Не запущен — docker compose up -d"
fi

echo; echo "API"
curl -sf http://localhost:8000/health >/dev/null && y "/health отвечает" || n "/health молчит"
ITEMS=$(curl -s http://localhost:8000/api/workspaces 2>/dev/null | grep -o '"label":"[^"]*"' | sed 's/"label":"//;s/"//' | tr '\n' ' ')
[ -n "$ITEMS" ] && y "Кнопки: $ITEMS" || n "/api/workspaces не отдаёт данные"

echo; echo "АГЕНТ"
LAST=$(docker compose logs --tail 500 backend 2>/dev/null | grep -E "Agent '.*' (connected|disconnected)" | tail -1)
if echo "$LAST" | grep -q "connected"; then y "Подключён"
elif echo "$LAST" | grep -q "disconnected"; then n "Отключён — запусти python agent.py на Windows"
else w "Не подключался с момента старта контейнера"; fi
# The backend logs a line only when a state value *changes* (mute, volume,
# device...) -- the per-second tick itself is no longer logged -- so an idle
# PC can legitimately show none here.
CNT=$(docker compose logs --tail 500 backend 2>/dev/null | grep -c "State changed")
[ "$CNT" -gt 0 ] && y "Изменения состояния видны ($CNT за последние 500 строк)" \
    || w "Изменений состояния в логе нет (нормально, если на ПК ничего не меняли)"
echo
