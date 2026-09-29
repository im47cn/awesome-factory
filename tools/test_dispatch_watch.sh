#!/bin/sh
# dispatch_watch.sh 正/负控制自测：证明 L1 守护「记录优先、只拦不可逆」语义真实存在。
# NC19 系（接续 test_pre-push-delete-guard.sh NC18 的编号；交付审查两轮修复的回归面：
# 尾斜杠所有权前缀、-z 解析的 quotepath/rename、末行无换行清单、JSON 转义、
# unborn HEAD 降级、参数校验、A 暂存态分类）。
# 每案例独立临时仓（git init，不触主仓），watcher 以 --interval 1 后台运行。
# 测试密封性（ADR-010）：hook 注入的 GIT_* 会劫持临时夹具仓的仓库发现；
# 顶层剥除，子进程继承（同 test_gauntlet_checks.sh）。
# shellcheck disable=SC2030,SC2031  # REPO/LOG/日志在各案例组子壳内赋值是有意隔离，父进程只读判定文件
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
      GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE

set -e
cd "$(dirname "$0")/.."

W=tools/dispatch_watch.sh
fails=0
ok()   { echo "  ok:   $1"; }
bad()  { echo "  FAIL: $1"; fails=$((fails + 1)); }

# 并发案例组（d–j）判定文件目录：各组后台子壳跑完把判定行（ok:/FAIL:）
# 写入 $CASES/<id>.verdict，父进程 wait 后按组序聚合；缺失/首行非判定
# 按 FAIL 计（案例组中途崩溃，fail-closed）。
CASES=$(mktemp -d /tmp/dwcases.XXXXXX)

# 建带基线提交的临时仓（含已跟踪文件，供 modified/deleted/rename 用例）
newrepo() {
  REPO=$(mktemp -d /tmp/dwtest.XXXXXX)
  git -C "$REPO" init -q
  git -C "$REPO" config user.email t@t.t
  git -C "$REPO" config user.name t
  echo base > "$REPO/tracked.txt"
  (cd "$REPO" && git add tracked.txt && git commit -qm base)
}
cleanup() { [ -n "${WPID:-}" ] && kill "$WPID" 2>/dev/null || true; [ -n "${REPO:-}" ] && rm -rf "$REPO" "$LOG" || true; return 0; }
# watcher 就绪握手：watcher_start 事件（dispatch_watch.sh 在初始快照紧邻之前
# emit 到 stdout=$LOG）落盘 ⇒ 进程已活、参数/工作树校验已过；再留 0.3s 余量
# 覆盖紧随的在飞初始 git status。上限 50×0.1s 后照常放行——watcher 未起的
# 场景由各组事件/哨兵断言 fail-closed 兜底（空日志必败）。替代固定 sleep：
# 七路并发下 CI 负载拉长启动时，固定等待会让变异落入基线（Sourcery #238）。
ready() { # ready <LOG>
  _n=0
  until grep -q '"event":"watcher_start"' "$1" || [ "$_n" -ge 50 ]; do
    sleep 0.1
    _n=$((_n + 1))
  done
  sleep 0.3
}
# ── NC19a 参数校验：非数字 interval → rc 1 ─────────────────────────────
newrepo; LOG=$(mktemp)
printf 'tests/\n' > "$REPO/own.txt"
if bash "$W" --dir "$REPO" --own "$REPO/own.txt" --interval abc >/dev/null 2>&1; then
  bad "NC19a 非数字 interval 应 rc 1"
else
  ok "NC19a 非数字 interval → rc 1"
fi
cleanup

# ── NC19b 参数校验：缺必需参数 → rc 1 ─────────────────────────────────
if bash "$W" --own /dev/null >/dev/null 2>&1; then
  bad "NC19b 缺参数应 rc 1"
else
  ok "NC19b 缺参数 → rc 1"
fi

# ── NC19c 非 git 目录 → rc 2 ──────────────────────────────────────────
if bash "$W" --dir /tmp --own /dev/null >/dev/null 2>&1; then
  bad "NC19c 非工作树应 rc 2"
else
  rc=$?
  if [ "$rc" = 2 ]; then ok "NC19c 非工作树 → rc 2"; else bad "NC19c rc=$rc 应为 2"; fi
fi

# ── 并发案例组（d–j）：各案例独立临时仓 + 独立 watcher，互不共享状态，
# 后台子壳整组跑完写判定文件，父进程按组序聚合。d–j 的 watcher 窗口
# 等待是本层主要成本（串行 sum≈31s），并发后实测层墙钟 33s→≈10s
#（瓶颈 = 最慢组 NC19f）；dispatch_watch.sh 的 --interval 只收正整数
# 秒，压窗口须动分发面产品脚本，超出本层范围。
(
  # ── NC19d unborn HEAD：warn 降级 + 正常记录 + rc 0 ─────────────────────
  REPO=$(mktemp -d /tmp/dwtest.XXXXXX); LOG=$(mktemp)
  git -C "$REPO" init -q
  printf 'a/\n' > "$REPO/own.txt"
  bash "$W" --dir "$REPO" --own "$REPO/own.txt" --interval 1 --max 2 > "$LOG" 2>&1
  rc=$?
  if grep -q unborn_head "$LOG" && [ "$rc" = 0 ]; then
    ok "NC19d unborn HEAD 降级运行 rc 0"
  else
    bad "NC19d rc=$rc 日志=$(cat "$LOG")"
  fi
  cleanup
) >"$CASES/d.verdict" 2>&1 &

(
  # ── NC19e owned 区静默：尾斜杠/无斜杠/末行无换行条目/rename 双端 owned ──
  # 哨兵：清单外文件必须恰好产生 1 条事件——证明「快照→diff→判定」链路活着，
  # 封死「watcher 未启动致日志空、零事件断言静默通过」的假过方向
  newrepo; LOG=$(mktemp)
  mkdir -p "$REPO/tests" "$REPO/tests2" && echo b > "$REPO/tests/t1.txt" && echo b > "$REPO/tests2/t1.txt"
  (cd "$REPO" && git add tests tests2 && git commit -qm more)
  printf 'tools/x.py\ntests/\ntests2\nold.txt' > "$REPO/own.txt"   # old.txt 无尾换行
  echo base > "$REPO/old.txt" && (cd "$REPO" && git add old.txt && git commit -qm old)
  bash "$W" --dir "$REPO" --own "$REPO/own.txt" --interval 1 --max 5 > "$LOG" 2>&1 &
  WPID=$!
  ready "$LOG"
  mkdir -p "$REPO/tools"
  echo x > "$REPO/tools/x.py"          # 文件条目 owned（目录折叠下有条目覆盖 → 跳过）
  echo n > "$REPO/tests/new.py"        # 尾斜杠条目内新增
  echo m >> "$REPO/tests/t1.txt"       # 尾斜杠条目内已跟踪改
  echo m >> "$REPO/tests2/t1.txt"      # 无斜杠条目已跟踪改
  (cd "$REPO" && git mv old.txt new.txt)   # 末行无换行条目 rename 双端 owned
  echo s > "$REPO/sentry.md"           # 哨兵：唯一应报的清单外事件
  wait $WPID
  n=$(grep -cE 'outside|owned_deleted' "$LOG")
  if [ "$n" = 1 ] && grep -q '"untracked_outside","path":"sentry.md"' "$LOG"; then
    ok "NC19e owned 区零事件 + 哨兵链路活着"
  else
    bad "NC19e 事件数=${n}（应恰 1 条哨兵）：$(grep -E 'outside|owned_deleted' "$LOG")"
  fi
  cleanup
) >"$CASES/e.verdict" 2>&1 &

(
  # ── NC19f 清单外四类事件：untracked/modified/deleted/rename(真 R) ──────
  newrepo; LOG=$(mktemp)
  printf 'tests/\n' > "$REPO/own.txt"
  # rename 素材在 watcher 启动前提交（运行中 commit 会触发 head_moved 提前退出）
  echo t > "$REPO/ren.txt" && (cd "$REPO" && git add ren.txt && git commit -qm ren)
  bash "$W" --dir "$REPO" --own "$REPO/own.txt" --interval 1 --max 8 > "$LOG" 2>&1 &
  WPID=$!
  ready "$LOG"
  echo s > "$REPO/stray.md"
  echo m >> "$REPO/tracked.txt"
  sleep 1.5
  rm "$REPO/tracked.txt"
  (cd "$REPO" && git mv ren.txt RENAMED.txt)
  wait $WPID || true
  n=0
  grep -q '"untracked_outside","path":"stray.md"' "$LOG" && n=$((n+1))
  grep -q '"modified_outside","path":"tracked.txt"' "$LOG" && n=$((n+1))
  grep -q '"deleted_outside","path":"tracked.txt"' "$LOG" && n=$((n+1))
  grep -q 'rename_outside.*ren.txt -> RENAMED.txt' "$LOG" && n=$((n+1))
  if [ "$n" = 4 ]; then ok "NC19f 四类越界事件全捕获"; else bad "NC19f 仅 $n/4：$(grep outside "$LOG")"; fi
  cleanup
) >"$CASES/f.verdict" 2>&1 &

(
  # ── NC19g 中文+引号路径：捕获 + 全行合法 JSON ──────────────────────────
  newrepo; LOG=$(mktemp)
  printf 'tests/\n' > "$REPO/own.txt"
  bash "$W" --dir "$REPO" --own "$REPO/own.txt" --interval 1 --max 5 > "$LOG" 2>&1 &
  WPID=$!
  ready "$LOG"
  mkdir "$REPO/中文外" && echo n > "$REPO/中文外/文档.md"
  mkdir "$REPO/带\"双引\"" && echo y > "$REPO/带\"双引\"/f.md"
  wait $WPID
  if grep -q '中文外' "$LOG" && grep -q '带\\"双引\\"' "$LOG" \
     && python3 -c '
import json,sys
for l in open(sys.argv[1]): json.loads(l)
' "$LOG" 2>/dev/null; then
    ok "NC19g 中文/引号路径捕获且 JSON 合法"
  else
    bad "NC19g 日志非法或缺失：$(head -5 "$LOG")"
  fi
  cleanup
) >"$CASES/g.verdict" 2>&1 &

(
  # ── NC19h head_moved + --kill-on-commit：kill + rc 3 ────────────────────
  newrepo; LOG=$(mktemp)
  printf 'tests/\n' > "$REPO/own.txt"
  zsh -c 'sleep 120' & VPID=$!
  bash "$W" --dir "$REPO" --own "$REPO/own.txt" --pid $VPID --interval 1 --max 10 --kill-on-commit > "$LOG" 2>&1 &
  WPID=$!
  ready "$LOG"
  echo c > "$REPO/c.txt" && (cd "$REPO" && git add c.txt && git commit -qm c)
  rc=0; wait $WPID || rc=$?
  if [ "$rc" = 3 ] && grep -q head_moved "$LOG" && grep -q '"killed"' "$LOG" \
     && ! kill -0 $VPID 2>/dev/null; then
    ok "NC19h head_moved → killed + rc 3"
  else
    bad "NC19h rc=$rc 日志=$(tail -3 "$LOG")"
  fi
  kill $VPID 2>/dev/null || true
  cleanup
) >"$CASES/h.verdict" 2>&1 &

(
  # ── NC19i head_moved 无 pid：rc 3，无 kill 段 ──────────────────────────
  newrepo; LOG=$(mktemp)
  printf 'tests/\n' > "$REPO/own.txt"
  bash "$W" --dir "$REPO" --own "$REPO/own.txt" --interval 1 --max 10 > "$LOG" 2>&1 &
  WPID=$!
  ready "$LOG"
  echo c > "$REPO/c.txt" && (cd "$REPO" && git add c.txt && git commit -qm c)
  rc=0; wait $WPID || rc=$?
  if [ "$rc" = 3 ] && grep -q head_moved "$LOG" && ! grep -q '"killed"' "$LOG"; then
    ok "NC19i head_moved → rc 3 无 kill"
  else
    bad "NC19i rc=$rc"
  fi
  cleanup
) >"$CASES/i.verdict" 2>&1 &

(
  # ── NC19j staged add（A 暂存态）→ untracked_outside 且 code 保留 XY ────
  newrepo; LOG=$(mktemp)
  printf 'tests/\n' > "$REPO/own.txt"
  bash "$W" --dir "$REPO" --own "$REPO/own.txt" --interval 1 --max 5 > "$LOG" 2>&1 &
  WPID=$!
  ready "$LOG"
  echo a > "$REPO/staged.md" && (cd "$REPO" && git add staged.md)
  wait $WPID
  if grep -q '"untracked_outside","path":"staged.md","detail":"code=A ' "$LOG"; then
    ok "NC19j A 暂存态归类新增"
  else
    bad "NC19j $(grep staged "$LOG")"
  fi
  cleanup
) >"$CASES/j.verdict" 2>&1 &

wait || :  # wait 状态不透传：组子壳崩溃（set -e 早退）时其 verdict 无判定行，
           # 由下方聚合按 FAIL 计（fail-closed）；此处非零早退反而绕过聚合与清理
# 按组序聚合判定（文件中第一条锚定格式的判定行）：bash 会对被信号杀死的
# 后台作业向 stderr 打 job 通知（NC19h 的 watcher kill 即此形态），判定行
# 之前可能有噪声行，不能按物理首行取。案例组中途崩溃（set -e 早退）→ 无
# 判定行 → FAIL（fail-closed）。
for _id in d e f g h i j; do
    _line=$(grep -m1 -E '^  (ok:|FAIL:)' "$CASES/$_id.verdict" 2>/dev/null) || _line=
    case "$_line" in
    '  ok:'*)   echo "$_line" ;;
    '  FAIL:'*) echo "$_line" >&2; fails=$((fails + 1)) ;;
    *)          echo "  FAIL: NC19$_id 案例组崩溃（无判定行）" >&2; fails=$((fails + 1)) ;;
    esac
done
rm -rf "$CASES"

echo
[ "$fails" = 0 ] && echo "dispatch_watch 全部负控制通过" || echo "$fails 项失败"
exit $fails
