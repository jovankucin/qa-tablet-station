"""The QA engine's tool-call gate (Step 3 stub): one decision per tool call.

decide(tool_name, tool_input) -> (decision, reason), decision in {"pass", "ask", "deny"}.

- deny: raw adb input, raw CDP Input events, old tap/CDP helper scripts (only act.py and
  qa/runner may drive devices), adb without -s, adb kill-server / forward --remove-all /
  forward without --no-rebind / port 9333, reading or copying qa/secrets.env, rewriting
  hashed records or the ledger, posting or editing ClickUp comments.
- ask: installs, uninstalls, pm clear, settings put, wifi changes, appops set,
  pm grant/revoke, reboot, deleting files on a device, ssh to webpc044 beyond status
  reads, every other ClickUp write, clickup_upload.py.
- deny (G1, 2026-10-22): git writes (push, commit, checkout, switch, reset, stash, worktree add, tag and
  branch writes), gh, GitHub write tools, and writes under WingtraCloud/ or WingtraPilot/. The shell scan
  also reads subshells, brace groups, if/for/while bodies, $(...), sh -c, eval, find -exec and wrappers
  (env, timeout, nice, sudo, xargs and their option values).
- ask: any other git subcommand that is not a known read, a connector tool whose name is write-shaped and that
  no rule here knows, and every ClickUp operator except the exact read pairs in CLICKUP_OPERATOR_READS.
  Also writes it cannot see through: xargs supplying the git subcommand, a relative shell write after cd into
  a clone, and git or gh in python/node/perl/ruby -c/-e code.
- ask (G2, 2026-10-23): the approval flag in any disguise (a prefix of --approved such as --a, quotes or backslashes
  inside it, a shell expansion in an act.py input call, inline code that runs act.py); Write, Edit and shell or
  inline-code writes to the engine's own safety files (qa/runner/, the app's Python, qa/plugin/, .claude/,
  qa/MANIFESTO.md, CLAUDE.md, ~/.claude/settings*.json, ~/.claude/scheduled-tasks/); on a tablet: rm, rmdir,
  unlink, find -delete, mv, cp, dd, truncate, a redirect into a file, content delete/insert/update, and adb push.
- pass: everything else (normal permission rules still apply).
- 2026-11-02 (audit item 20): "adb without -s" counts only adb that the line runs as a program: the first word of a
  simple command (after keywords, env assignments, redirections and the wrappers above), the command line of sh -c,
  eval, find -exec, ssh and watch, and the command of a subprocess/os.system-style call in inline code. A mention in a
  grep pattern, an echo, a comment, a file name or a data heredoc no longer denies. A line or code the gate cannot read
  (an unclosed quote, code that is not Python, a command built at run time) keeps the old reading: every mention
  counts. The other adb rules (input, kill-server, forward, install, push, device writes) still read every mention.
  Review (same day): a mention is text only among the arguments of a program that only reads or prints them
  (TEXT_PROGRAMS: grep, echo, cat, which, git log, ...). It keeps the old reading in any other program's arguments
  (arch, script, parallel, screen, noglob, ...), in an env assignment or a wrapper's option, and on the whole line when
  a shell without -c, an interpreter without -c/-e or a heredoc, xargs or osascript-like runners take text the gate
  cannot read (a pipe, a here-string, <(...), a script). sh -c is read past -- and other options; inline Python that
  renames, passes on or looks up a run function keeps the old reading; -s counts only before adb's command.

- 2026-11-03 (filing a ticket the person approved in the app): the one pass for --approved. A Bash command that is
  exactly one simple command QA_TICKET=<tests/<slug>/tickets/<n>> python3 -I qa/runner/act.py <TA5|ET65|W044> tap
  <text:Report bug|text:Next> [--expect X] [--timeout N] [--find-timeout N] --approved, with nothing the shell expands
  (no $, backtick, backslash, glob, redirect, separator or subshell outside quotes), passes when
  <QA>/runner/state/approvals/ holds an approval for that ticket and target: signed with <QA>/runner/state/approval.key
  (HMAC-SHA256 over the other fields), by touch_id, created <= now < expires (at most 30 min after created), no
  <id>.used marker, and draft_sha256 equal to sha256(title.txt + "\n" + description.txt) now. The Next tap writes
  <id>.used (atomic, exclusive), so an approval files one ticket once. The command must also pass every other rule;
  anything else keeps today's decision (ask, or deny where it denied).
  deny: any tool call that names the approval key (approval.key in any quoting, a glob in runner/state/ that matches it,
  cd into runner/state or approvals/, a recursive read or copy of runner/state, Read/Grep/Write/Edit of the key), and
  any write into runner/state/approvals/ or of the runner/state folder itself (a shell command naming an approvals
  path or runner/state that is not a plain read, a redirect there, inline code naming it, Write/Edit/MultiEdit/
  NotebookEdit there). Only the app server and the gate write approvals.

- 2026-11-03 security review of the approval path: deny writes of the compiled Touch ID prompt (qa/app/.touchid, a
  line naming it where a command is not a plain reader, and Write/Edit of it); ask on writes of touchid.swift, the app's
  __pycache__ and a move or removal of a whole engine folder (qa, qa/runner, qa/app, qa/plugin, .claude); deny File
  ticket (api/tickets/file) from a shell line that is not a plain reader and from any connector or browser tool; ask on
  act.py JS (a read's expression, a js: readback) that may act in the page (click, submit, send, set a value, build
  code) or that the line computes at run time, also on the approval pass; ask on find -delete/-exec or rm -r over
  runner/state or a folder above it; and no approval passes for a ticket with submitted.json or filed.json.

- 2026-11-03 (Post comment on the Results page: the app server posts a test's results comment to ClickUp after the
  person's press and Touch ID): deny the Post comment route (api/results/comment/post) and the app's ClickUp comment
  poster (qa/app/clickup_post.py, as a module or a file name) from a shell line that is not a plain reader, and from
  any connector or browser tool, so only the person's press in the page can start a post. Review (same day): also any
  path below api/results/comment/, the app routes with URL dot segments (/./, x/../), the poster as an attribute
  (serve.clickup_post), and ClickUp's own comment endpoints over REST (api.clickup.com with /comment).

- 2026-11-16 (the person's OK to fewer prompts in tablet testing, "Yes, go ahead"): one pass. A command that is
  nothing but plain test-file pushes passes: adb -s <serial> push of regular files tests/<folder>/files/<name> of the
  workspace into /sdcard/Download/QA-<name>/ or /sdcard/Download/WingtraQA/ (optionally with a file name), joined by
  ' && ' with adb -s <serial> shell mkdir -p / ls -la of such a folder, judged on the raw text (_test_file_push). Any
  other push still asks. The first version, judged on the reshaped segment text, and a pass for cloud reads through the
  app (CapacitorHttp.get at the API base) were dropped after an adversarial review found ways round both. Also a
  bracket access to a Capacitor plugin (Plugins[...]) in act.py JS now asks.

- 2026-11-16 (the person's OK, "Yes, fix them": three older holes the reviews of that day found). (1) adb words are
  read as the shell joins them (quotes and backslashes inside a word dropped: p''ush is push), and the adb command is
  found past its global options (-d, -e, -t <id>, --exit-on-write-error); any other option (-H, -P, -L, -a, nodaemon
  and the rest) or a command word the gate cannot read ($, a glob) asks, with the deny rules tried at every word, so
  adb -s X -t 1 push, -H host push or p\\ush no longer slip past the push, kill-server and raw input rules. (2) A
  tablet command: words the shell joins are one word ('r'm), /system/bin/rm counts as rm, tee, sed -i, sort -o,
  logcat -c and 1>/&> redirects (outside single quotes) count as writes; the programs the tablet line runs are read
  where a command starts (past keywords, NAME=value, env/nice/timeout/toybox/xargs/run-as, find -exec): a shell, eval,
  alias, su, sendevent (deny), monkey, touch, mktemp, dos2unix, a tar or unzip that unpacks, a screencap or
  screenrecord into a file, awk with system()/getline/print >, sed w, and a program computed at run time ($, a
  backtick, braces, a glob) ask; so do adb exec-in, adb logcat -c, an adb shell with no command (it runs what this
  Mac pipes into it), and a $ value on a line that also holds a write; raw input through exec-out is denied like
  through shell. (3) act.py page JS is read by a small lexer (_js_clean) instead of a string regex: strings, regexes
  and comments are read as the browser reads them, and a with statement, a backslash, # or @ outside a string, or a
  / the gate cannot place as a regex or a division asks, so /*'*/ ... /*'*/, /'/ and with(document.body)click() no
  longer hide a click; a read's option values are not taken for JS, its unknown -words and every word after -- are,
  and a ; glued to the read's last quote ends it. An adversarial review the same day found ~60 new prompts on normal
  tablet commands in the first version (a $ or backslash in a tablet grep, awk, grep -i source, ls /system/bin/sh,
  screencap -d 0, text that only names adb logcat -c); the reading above keeps them passing.

- 2026-11-17 (the person's OK, "Yes, add them"): eleven page calls act.py JS could make without a prompt now ask:
  .constructor (code from a string), location.replace, new Audio/Image/Worker and other constructors that load a URL,
  document.write, indexedDB.deleteDatabase, a bare open() or postMessage, .onclick() and other on<event> handlers,
  form.reset(), serviceWorker.register(), with their close relatives (set*, delete*, remove*, write*, load) and their
  bracket spellings (x['constructor'], self['open'], this['fetch']). See JS_ACTION. Same day, own review (the subagent
  reviews of rounds 2 and 3 were stopped by a safety check before they probed anything): the adb rules now also run on
  each adb call as the shell reads it (_adb_rule; a backslash-newline after adb -s X hid push, kill-server and raw
  input, also from the old gate); $'...' or $"..." quoting on an adb line asks; on the tablet line, sh -c / eval / trap /
  watch / awk system(), getline or print into a file ask wherever they stand (a single-quoted redirect handed to
  logwrapper sh -c passed); logwrapper, toolbox and prlimit are wrappers, exec -a takes a value; uniq with an output
  file, cmd package clear (pm under another name) and settings delete/reset ask. After it went live, a check of 536
  normal commands found 75 new prompts, fixed the same day: a $ value is judged only when this Mac fills it in, and then
  only the line's assignments, loop lists, here-strings and $(...) texts (not this Mac's own | tee or an awk NR>1);
  $(...) on the tablet is one word with its command judged on its own, case patterns are no commands; this Mac's \\"
  escapes are read as the shell reads them; on the real-call pass only rules at adb's command apply (a grep for
  reboot in a quoted tablet pipeline no longer asks), while pm/cmd package/settings/svc/reboot are read where they
  run on the tablet; awk's own strings and system_server are text; gzip in a pipe writes nothing; eval/trap/watch
  count only where a command starts; a one-string page key is read as the member it names (x['plane'] is a read,
  x['constructor'] asks), .constructor.name and .online are reads; xargs asks only when its words become adb's
  command or reach the tablet line from text the line made up.

The gate reads text only: git or gh run from a script file or a heredoc fed to an interpreter, or under a
program name held in a variable, is out of its reach; so is adb run that way, and so is the approval key read or an
approval written from a script file, through a variable, or by a recursive read of a parent folder.

Used by qa/runner/gate_hook.py (the PreToolUse hook) and, under QA_RUNNER=1, by act.py.
"""
import ast
import codecs
import datetime
import fnmatch
import hashlib
import hmac
import json
import os
import re
import shlex
import textwrap
import unicodedata
import urllib.parse

QA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.dirname(QA_DIR)

ALLOWED_DRIVERS = re.compile(r"(?:^|[\s/])qa/runner/[\w/]+\.py\b|(?:^|[\s/])act\.py\b")
OLD_HELPERS = re.compile(r"(?:^|[\s/}'\"])(?:ui|dlg|pick|record|cdp|maps_import|import_flightplan|inflight_upload|open_flight|"
                         r"open_import_file|run_flight_matrix|fill_report|find_tap|qfind|apiread|geo2screen|field_tap)\.py\b")
CDP_INPUT = re.compile(r"Input\.(?:dispatch\w+|insertText|emulateTouchFromMouseEvent|synthesize\w+)")
SEPARATORS = re.compile(r"(?:\|\||&&|;|\||\n|\$\(|`)")
ADB_ACTION_ASK = [
    (re.compile(r"\b(install|install-multiple|install-multi-package|uninstall)\b"), "installs or uninstalls an app"),
    # 2026-11-17: cmd package is pm under another name (cmd package clear wipes the app's data); settings delete/reset
    (re.compile(r"\b(?:pm|cmd\s+package)\s+(clear|install|uninstall|grant|revoke|disable|enable|suspend)\b"),
     "changes an installed package"),
    (re.compile(r"\bsettings\s+(put|delete|reset)\b"), "changes an Android setting"),
    (re.compile(r"\b(cmd|svc)\s+wifi\b"), "changes Wi-Fi"),
    (re.compile(r"\bappops\s+set\b"), "changes an app-op permission"),
    (re.compile(r"\breboot\b"), "reboots the tablet"),
    (re.compile(r"\brm\s+(-\w+\s+)*/(sdcard|storage|data)\b"), "deletes files on the tablet"),
]
# 2026-11-16: read only on adb calls the line really runs (a mention in rg, grep or echo text is no call), at the adb
# command: exec-in feeds a file or stream from this Mac into a command on the tablet; logcat -c wipes the tablet's log.
ADB_REAL_CALL_ASK = [
    (re.compile(r"exec-in\b"), "runs a command on the tablet with input from this Mac (it can write files)"),
    (re.compile(r"logcat\b[^;&|]*?(?<=\s)(-[a-zA-Z]*[cfG]\b|--clear|--file|--buffer-size)"),
     "clears, resizes or writes the tablet's log (logcat -c wipes the evidence)"),
]
# 2026-11-16: adb's global options before its command (-s is taken out before), a command word computed at run time,
# and why an adb call with an unread option or such a word asks.
ADB_PLAIN_OPTIONS = {"-d", "-e", "--exit-on-write-error"}
ADB_VALUE_OPTIONS = {"-s", "-H", "-P", "-L", "--one-device", "--reply-fd"}
ADB_COMPUTED_WORD = re.compile(r"[$*?`]")
# xargs that feeds adb its words (echo push ... | xargs adb -s X): adb's command comes from stdin
ADB_FROM_XARGS = re.compile(r"\bxargs\b[^|;&\n]*\badb\b")
ADB_UNREAD_REASON = ("adb with a global option the gate does not read (-a, nodaemon, server, ...) or a command word "
                     "computed at run time ($, a glob): it could hide a push, a kill-server or raw input; needs your OK")
ADB_NO_DEVICE = re.compile(r"^(devices|version|start-server|help|--version|-?-help|forward\s+--list|mdns)\b")
WEBPC044_READS = re.compile(
    r"^\s*(echo|sw_vers|ipconfig\s+getifaddr|netstat|ps|nc\s+-z|grep|cat\s+~?/?\S*qa-chrome\.log|ls|shasum|"
    r"perl\s+-MTime::HiRes|whoami|date|ifconfig|awk|printf|true|uname|sed\s+-n|head|tail|wc|for\s|do\s|done|then|fi|if\s|"
    r"test\s|\[|\]|sort|uniq|cut|tr)\b")
CLICKUP_READ = re.compile(r"clickup_(get_\w+|search\w*|filter_tasks|find_member_by_name|resolve_assignees|list_\w+|"
                          r"download_\w+|get_operators|get_schema)$")
CLICKUP_COMMENT = re.compile(r"clickup_(create_comment|create_task_comment|update_comment|delete_comment|"
                             r"react_to_chat_message|send_chat_message)$")
RECORD_PATH = re.compile(r"(^|/)qa/(cases|runs|issues|devices)/.+\.json$|(^|/)qa/ledger/")
# ClickUp Unified API operators that only read, as exact <model>.<operator> pairs (clickup_get_operators, 2026-10-22).
CLICKUP_OPERATOR_READS = {"task.get_many"}

# Source clones are read-only (G1): no Write/Edit or shell write under them.
CLONE_PATH = re.compile(r"(^|/)(WingtraCloud|WingtraPilot)(/|$)")
CLONE_SHELL_WRITE = re.compile(r"(^|[\s(;&|`])(>>?|rm\s|mv\s|cp\s|sed\s+-i|truncate\s|tee\s|touch\s|ln\s)[^;|&]*"
                               r"(WingtraCloud|WingtraPilot)/|\w>>?\s*[\"']?[^\s;|&\"'<>]*(WingtraCloud|WingtraPilot)/")
CLONE_REASON = "the WingtraCloud and WingtraPilot clones are read-only (G1)"
CD_CLONE = re.compile(r"(^|[\s;&|(])(cd|pushd)\s+(-\w+\s+)*(\"[^\"]*|'[^']*|[^\s;&|\"']*)(WingtraCloud|WingtraPilot)"
                      r"(?=[/\"'\s;&|)]|$)")
# Redirect targets that can be file names (not &fd, not regex text like >[A-Z] inside a quoted pattern).
REDIRECT_TARGET = re.compile(r"(?:^|\s)(?:\d+|&)?>>?\s*[\"']?([\w.$~/-][^\s;|&<>\"']*)")
ASSIGN = re.compile(r"(?:^|[\s;&|(])(?:export\s+)?([A-Za-z_]\w*)=[\"']?(\$\{?(\w+)|[/~])?")
HEREDOC_BODY = re.compile(r"(<<-?\s*([\"']?)(\w+)\2[^\n]*\n)[\s\S]*?^\s*\3[ \t]*$", re.M)
WRITE_VERBS = {"rm", "mv", "cp", "ln", "tee", "touch", "truncate"}
INTERPRETER = re.compile(r"(^|[\s;&|(/])(python[\d.]*|node|nodejs|perl|ruby)[ \t]+")
CODE_FLAG = re.compile(r"^(-\w*[ceE]|--eval|--print)(=|$)")
# Simple-command boundaries for reading write targets: like SPLIT but keeping ( ) inside quoted sed/awk scripts.
WRITE_SPLIT = re.compile(r"\|\||\|&|&&|;|\||\n|(?<![<>&|])&(?![>&])")
# In inline code: gh, or git followed anywhere later by a word of a repository write.
CODE_GH = re.compile(r"(?<![\w.-])gh(?![\w.-])")
CODE_GIT = re.compile(r"(?<![\w.-])git(?![\w.@-])")
CODE_GIT_WRITE = re.compile(r"\b(push|commit|checkout|switch|reset|stash|tag|branch|worktree|merge|rebase|pull|"
                            r"cherry-pick|revert|restore|clean|submodule)\b")
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
GIT_READS = {"fetch", "log", "show", "grep", "describe", "merge-base", "status", "diff", "rev-parse", "rev-list",
             "ls-tree", "ls-files", "cat-file", "for-each-ref", "blame", "shortlog", "name-rev", "show-ref", "version",
             "help", "cherry", "range-diff", "whatchanged", "count-objects", "check-ignore", "var", "ls-remote"}
GIT_WRITES = {"push", "commit", "checkout", "switch", "reset", "stash"}
GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
                         "--config-env"}
GIT_BRANCH_WRITE = re.compile(r"^(-d|-D|--delete|-m|-M|--move|-c|-C|--copy|-f|--force|-u|--set-upstream-to|"
                              r"--unset-upstream|--edit-description|--track|--no-track|--create-reflog)(=|$)")
GIT_BRANCH_LIST = re.compile(r"^(-l|--list|-r|--remotes|-a|--all|--contains|--no-contains|--merged|--no-merged|"
                             r"--points-at|--show-current)(=|$)")
GIT_TAG_LIST = re.compile(r"^(-l|--list|--contains|--no-contains|--merged|--no-merged|--points-at|--sort|--format|"
                          r"-n\d*|--column|--no-column|--color|-i|--ignore-case)(=|$)")
REDIRECT = re.compile(r"^\d*(>>?|<|>&|&>)")
# ---- G2 (2026-10-23): gaps found while planning approvals from the app ----
# act.py's only option that starts with --a is --approved; any prefix of it counts as the flag. 2026-11-03: also when a
# shell operator ends the word (--approved; ls, --approved&&, --approved|cat, (... --approved), --approved>out): before,
# those passed with no prompt.
APPROVED_PREFIX = re.compile(r"(?:^|\s)--a(?:p(?:p(?:r(?:o(?:v(?:e(?:d)?)?)?)?)?)?)?(?=[\s=;&|()<>`}]|$)")
ACT_CALL = re.compile(r"(?:^|[\s/])act\.py\b")
ACT_VERB = re.compile(r"(?:^|[\s/])act\.py\s+(\S+)\s+(\S+)")
ACT_INPUT_VERBS = {"tap", "type", "key", "sys-tap", "choose-file", "map-tap"}  # map-tap: 2026-11-03, drawing on the map
ACT_REF = re.compile(r"act\.py|\bimport\s+act\b|\bfrom\s+act\s+import\b|__import__\(\s*['\"]act['\"]")
CODE_RUNS = re.compile(r"\bsubprocess\b|\bos\.(system|exec\w*|spawn\w*|popen)\b|\bPopen\b|\brunpy\b|\bimport\s+act\b|"
                       r"\bfrom\s+act\s+import\b|__import__")
# The engine's own safety files: the gate and the runner, the app's writer and server, the plugin's skills, the
# settings that load the hooks, the rules, and the scheduled tasks that start sessions. ~/.claude/projects (memory) is
# not one of them.
HOME = os.path.expanduser("~")
# 2026-11-03 security review: the Touch ID prompt (touchid.swift, its build .touchid) and the app's compiled Python
# (__pycache__, which Python loads in place of a .py whose mtime and size it records) are safety files too: a helper
# that always exits 0, or a planted approvals .pyc, would let the model approve its own ticket.
SAFETY_REL = re.compile(r"^(qa/runner/|qa/app/(?:[^/]+\.py|touchid\.swift|\.touchid[^/]*|__pycache__/.*)$|qa/plugin/|"
                        r"\.claude/|qa/MANIFESTO\.md$|CLAUDE\.md$)")
SAFETY_HOME = re.compile(r"^" + re.escape(HOME) + r"/\.claude/(settings[^/]*\.json$|scheduled-tasks/)")
# A move or removal of a whole folder that holds them counts too (mv qa/runner away, then a new one in its place).
_SAFE_T = (r"[\"']?(?:(?:" + re.escape(WORKSPACE) + r"/)?(?:qa/runner/|qa/app/(?:[\w-]+\.py|touchid\.swift|\.touchid|"
           r"__pycache__/)|qa/plugin/|\.claude/(?!projects/)|(?:qa(?:/runner|/app|/plugin)?|\.claude)/?(?=[\s\"';&|)]|$)|"
           r"qa/MANIFESTO\.md|CLAUDE\.md)|(?:~|\$HOME|\$\{HOME\}|" + re.escape(HOME) + r")/\.claude/"
           r"(?:settings[\w.-]*\.json|scheduled-tasks/))")
# rm, mv, tee, touch, truncate, sed -i, chmod, patch and > write the file they name wherever it stands; cp, rsync, ln
# and install write only their last operand.
SAFETY_SHELL_WRITE = re.compile(
    r"(?:^|[\s(;&|`])(?:>>?|rm\s|mv\s|sed\s+-i|truncate\s|tee\s|touch\s|chmod\s|patch\s)[^;|&\n]*?(?<![\w./-])" + _SAFE_T +
    r"|(?:^|[\s(;&|`])(?:cp|rsync|ln|install)\s[^;|&\n]*\s(?<![\w./-])" + _SAFE_T + r"[^\s;|&]*[\"']?\s*(?:$|[;|&\n)])")
SAFETY_LITERAL = re.compile(r"[\"'](?:" + re.escape(WORKSPACE) + r"/)?(?:qa/runner/[\w./-]+|qa/app/[\w-]+\.py|"
                            r"qa/app/(?:touchid\.swift|\.touchid[\w.]*|__pycache__/[\w./-]*)|"
                            r"qa/plugin/[\w./-]*|\.claude/(?!projects/)[\w./-]*|qa/MANIFESTO\.md|CLAUDE\.md)[\"']")
CODE_WRITE = re.compile(r"open\([^)]*?,\s*[rbf]*[\"'][wax]|\.write_(text|bytes)\(|\bshutil\.(copy\w*|move)\(|"
                        r"\bos\.(replace|rename|remove|unlink)\(|\.(unlink|rename|replace)\(")
SAFETY_REASON = ("changes the QA engine's own safety files (gate, runner, app writer and server, skills, settings, "
                 "rules, scheduled tasks): needs your OK")
# Device-side commands that delete, move or overwrite files on a tablet (adb shell / exec-out).
ADB_SEP_TOKENS = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&", "\n"}
# 2026-11-16 (the person's OK, "Yes, fix them"): tablet writes. DEVICE_WRITES are read anywhere on the tablet line, as
# typed and with its quotes and backslashes dropped ('r'm, r\m); a command word also starts after / (/system/bin/rm), =
# (awk -v c=rm), { } and !; tee, sed -i, sort -o, logcat -c and 1>/2>/&>/>|/<> redirects count. The programs a tablet
# line runs (the DEVICE_* sets below, _device_program_risk) are judged only where a command starts, so grep -i source or
# ls /system/bin/sh stay reads (after the review of the same day: word-anywhere lists asked on such reads).
DEVICE_WORD = r"(^|[\s;&|(){}!=`/])"
DEVICE_REDIRECT = re.compile(r"(?:&>>?|\d*(?:>>?|>\||<>))\s*(?!(?:/dev/null|&(?:\d+|-))(?![^\s;&|)]))\S")
DEVICE_WRITES = [
    (re.compile(DEVICE_WORD + r"(rm|rmdir|unlink)(\s|$)"), "deletes files on the tablet"),
    (re.compile(DEVICE_WORD + r"find\s.*-(delete|exec(dir)?\s+rm)\b"), "deletes files on the tablet"),
    (re.compile(DEVICE_WORD + r"(mv|cp|dd|truncate|tee)(\s|$)"), "moves, copies over, truncates or writes files on the tablet"),
    (re.compile(DEVICE_WORD + r"sed\s[^;&|\n]*?(?<=\s)(-[A-Za-z]*i|--in-place)"), "edits a file in place on the tablet"),
    (DEVICE_REDIRECT, "writes a file on the tablet"),
    (re.compile(DEVICE_WORD + r"content\s+(delete|insert|update)\b"), "changes a content provider on the tablet"),
    (re.compile(DEVICE_WORD + r"sort\s[^;&|\n]*?(?<=\s)(-o|--output)"), "writes a file on the tablet"),
    (re.compile(DEVICE_WORD + r"logcat\s[^;&|\n]*?(?<=\s)(-[a-zA-Z]*[cfG]\b|--clear|--file|--buffer-size)"),
     "clears, resizes or writes the tablet's log (logcat -c wipes the evidence)"),
]
# Programs judged where a command starts on the tablet line (_device_program_risk).
DEVICE_SHELLS = {"sh", "bash", "mksh", "ash", "dash", "zsh", "su", "eval", "source", ".", "alias", "trap", "watch"}
# Anywhere on the tablet line: text that a shell runs as commands (sh -c '...', eval, system() in awk), whichever
# program stands in front of it (logwrapper sh -c '... > f'); then a single-quoted redirect counts too.
DEVICE_RUNS_TEXT = re.compile(r"(^|[\s;&|(){}!=`/])(?:sh|bash|mksh|ash|dash|zsh|su)\s+(?:-\S+\s+)*-[a-zA-Z]*c(\s|$)|"
                              r"\bsystem\s*\(|\bgetline\b")
DEVICE_FILE_WRITERS = {"touch", "mktemp", "dos2unix", "unix2dos", "split", "ln", "link", "mkfifo", "mknod", "fallocate",
                       "shred", "uudecode", "patch", "cpio", "install"}
DEVICE_PACKERS = {"gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "lz4", "zstd", "unzstd"}
DEVICE_KEYWORDS = {"if", "then", "elif", "else", "fi", "while", "until", "do", "done", "case", "esac", "in", "!", "{", "}",
                   "time", "function"}
# Wrappers that run the command after them: (their options that take a separate value, their operands before it).
DEVICE_WRAPPERS = {"exec": ({"-a"}, 0), "command": (set(), 0), "nohup": (set(), 0), "setsid": (set(), 0),
                   "logwrapper": (set(), 0), "toolbox": (set(), 0), "prlimit": (set(), 0),
                   "toybox": (set(), 0), "busybox": (set(), 0), "env": ({"-u", "-C", "-S"}, 0), "nice": ({"-n"}, 0),
                   "ionice": ({"-c", "-n", "-p"}, 0), "timeout": ({"-s", "-k"}, 1), "chrt": (set(), 1),
                   "taskset": (set(), 1), "stdbuf": ({"-i", "-o", "-e"}, 0), "run-as": (set(), 1),
                   "xargs": ({"-I", "-n", "-s", "-P", "-E", "-L", "-d", "-a"}, 0)}
SCREENRECORD_VALUE_OPTIONS = {"--size", "--bit-rate", "--time-limit", "--display-id", "--codec-name", "--output-format",
                              "--bframes", "--max-size"}
# Simple-command boundaries: ;, &, |, |&, &&, ||, newline, ), $( and backticks; not the & of 2>&1, >&2 or &>.
SPLIT = re.compile(r"\|\||\|&|&&|;|\||\n|\$\(|`|\)|(?<![<>&|])&(?![>&])")
SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
SHELL_WORDS = {"{", "}", "!", "if", "then", "elif", "else", "do", "while", "until", "coproc"}
# Wrappers that run the command after them: (their options that take a separate value, operands before the command).
WRAPPERS = {"env": ({"-u", "--unset", "-C", "--chdir", "-P"}, 0), "command": (set(), 0), "exec": ({"-a"}, 0),
            "nohup": (set(), 0), "time": ({"-f", "-o"}, 0), "nice": ({"-n"}, 0), "stdbuf": (set(), 0),
            "sudo": ({"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U"}, 0), "caffeinate": ({"-t", "-w"}, 0),
            "xargs": ({"-I", "-J", "-L", "-n", "-P", "-R", "-S", "-s", "-E", "-d", "-a"}, 0),
            "timeout": ({"-s", "-k", "--signal", "--kill-after"}, 1), "ionice": ({"-c", "-n", "-p"}, 0)}
SHELLS = {"bash", "sh", "zsh", "dash"}
# GitHub MCP tools that write (any server; the names are GitHub's).
GITHUB_WRITES = {"add_comment_to_pending_review", "add_issue_comment", "add_reply_to_pull_request_comment",
                 "create_branch", "create_or_update_file", "create_pull_request", "create_repository", "delete_file",
                 "fork_repository", "issue_write", "merge_pull_request", "pull_request_review_write", "push_files",
                 "request_copilot_review", "sub_issue_write", "update_issue_comment", "update_pull_request",
                 "update_pull_request_branch"}
# Connector tools whose name says they change something. The app's own tools (ccd_*, the browser pane, the
# simulator, terminal, visualize, Chrome, scheduled tasks) are Claude's UI, not external resources.
WRITE_SHAPED = re.compile(r"^(create|update|delete|remove|merge|push|fork|write|send|post|upload|attach|move|rename|"
                          r"add|set|submit|publish|share|archive|unarchive|clear|import|export|batch|edit|modify|patch|"
                          r"put|insert|append|replace|transfer|invite|grant|revoke|assign|close|reopen|react|reply|"
                          r"restore|duplicate|trigger|execute|run|start|stop)(_|$)")
INTERNAL_MCP = re.compile(r"^mcp__(ccd_\w+|Claude_Browser|Claude_Code_iOS_Simulator|terminal|visualize|"
                          r"claude-in-chrome|scheduled-tasks|mcp-registry|plugin-authoring)__")
# ---- 2026-11-02 (audit item 20): the -s rule counts adb only where the line runs it ----
# A quoted heredoc on a line naming one of these is a script, not data.
SH_READER = re.compile(r"(^|[\s;&|(/])(bash|sh|zsh|dash|ssh)\b")
# Programs that run a command line of their own: (options that take a value, operands before the command line).
LINE_RUNNERS = {"ssh": ({"-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l", "-m", "-O", "-o",
                         "-p", "-Q", "-R", "-S", "-W", "-w"}, 1),
                "watch": ({"-n", "--interval"}, 0)}
# Inline-code calls that run a program or a shell line: Python's subprocess, os, pty and asyncio, and the same names in
# Node, Perl or Ruby code that happens to parse as Python.
CODE_RUN_CALL = re.compile(r"^(run|call|check_call|check_output|Popen|getoutput|getstatusoutput|system|popen\w*|"
                           r"exec\w*|spawn\w*|posix_spawnp?|create_subprocess_\w+|capture\w*|pipeline\w*)$")
# The same functions when the code passes them on, renames or looks them up instead of calling them (2026-11-02 review;
# narrower than CODE_RUN_CALL, so sys.executable or cursor.execute stay plain names).
CODE_RUN_REF = re.compile(r"^(run|call|check_call|check_output|Popen|getoutput|getstatusoutput|system|popen\w*|exec|"
                          r"exec[lv]p?e?|execSync|execFile(Sync)?|spawn\w*|posix_spawnp?|create_subprocess_\w+)$")
# A redirection word: the operator alone (its file is the next word) or with its file glued on (>out, 2>&1).
REDIRECT_WORD = re.compile(r"^\d*(?:<<<|>>|>\||<>|>&|<&|&>>|&>|>|<)")
SERIAL_ARG = re.compile(r"(^|\s)-s\s+\S+")
SUBST_MARK = "\x00"  # stands in a word for the output of $(...) or `...`
# 2026-11-04: _sh_scan(glob_mark=True) keeps a * ? [ or { that stands outside quotes as one of these (private-use
# characters, apart from SUBST_MARK and HEREDOC_MARK)
GLOB_MARKS = {"*": "", "?": "", "[": "", "{": ""}
HEREDOC_MARK = "\x01"  # added to a command's words when a heredoc feeds it
ADB_WORD = re.compile(r"(?<![\w.-])adb(?![\w.-])")
# ---- 2026-11-02 review of item 20: what may run adb although adb is not its first word ----
# Programs that only read, print or store their arguments: adb among them is text. The set holds the options whose
# value is a program the program runs (that value is not text). Any other program that names adb in its arguments may
# run it (arch, script, parallel, screen, a zsh precommand like noglob, ...), so that command keeps the old reading.
TEXT_PROGRAMS = dict.fromkeys(
    ("echo", "printf", "grep", "egrep", "fgrep", "ag", "ack", "sed", "awk", "cat", "head", "tail", "wc", "uniq", "cut",
     "tr", "ls", "which", "type", "whereis", "file", "stat", "basename", "dirname", "realpath", "readlink", "test", "[",
     "true", "false", ":", "jq", "diff", "cmp", "comm", "find", "mkdir", "touch", "tee", "column", "nl", "pgrep", "ps",
     "lsof", "shasum", "sha256sum", "strings", "date", "cd", "pwd", "gawk", "nawk", "mawk", "for", "select", "case"),
    frozenset())  # for/select/case: their word lists and patterns (a loop variable that runs is a computed program)
AWKS = {"awk", "gawk", "nawk", "mawk"}
TEXT_PROGRAMS.update(rg=frozenset({"--pre"}), sort=frozenset({"--compress-program"}), ack=frozenset({"--pager"}),
                     git=frozenset({"-c", "--config-env", "--exec-path", "-O", "--open-files-in-pager", "--extcmd",
                                    "-x", "--exec"}))
# Shells: without -c they run a script from a file, stdin, a pipe, a here-string or <(...), which the gate cannot read.
SH_PROGRAMS = SHELLS | {"ksh", "mksh", "fish", "csh", "tcsh", "source", "."}
SH_VALUE_OPTS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
# Interpreters whose -c/-e code the code scan reads; without it they read a script file or stdin.
INTERP_PROG = re.compile(r"^(python[\d.]*|node|nodejs|perl|ruby)$")
INTERP_VALUE_OPTS = {"-W", "-X", "-r", "--require"}
# Programs that run commands from their arguments or stdin in a language the gate does not read.
STDIN_RUNNERS = {"osascript", "at", "batch", "parallel", "expect", "tclsh", "wish", "lua", "php", "Rscript"}
# ---- 2026-11-03: filing a ticket the person approved in the app (one press plus Touch ID) ----
# QA_DIR (the approvals, the key, and the tickets under its parent) and _now() are module attributes so the tests can
# point them at a temporary folder and a fixed clock; nothing outside the tests changes them (no environment variable).
APPROVAL_TARGETS = {"TA5", "ET65", "W044"}
APPROVAL_SELECTORS = {"text:Report bug", "text:Next"}
APPROVAL_FIELDS = {"id", "ticket_dir", "draft_sha256", "target", "created", "expires", "by", "sig"}
APPROVAL_ID = re.compile(r"^A-\d{14}-[0-9a-f]{6}$", re.A)
APPROVAL_TTL = datetime.timedelta(minutes=30)
TICKET_DIR = re.compile(r"^tests/\d{4}-\d{2}-\d{2}-[a-z0-9-]+/tickets/[0-9]+$", re.A)
# What may stand outside quotes in the filing tap: nothing the shell expands, globs, redirects or separates.
TICKET_PLAIN = re.compile(r"[\w./:=,+@%-]", re.A)
TICKET_VALUE_OPTS = {"--expect", "--timeout", "--find-timeout"}
APPROVAL_KEY = "approval.key"
KEY_REASON = ("names the approval key, which signs the person's Touch ID OK: only the app server and the gate read it, "
              "the model never reads or prints it")
APPROVALS_REASON = "writes approvals: only the app server and the gate write runner/state/approvals/, never the model"
# cd or pushd into runner/state or its approvals folder (a later call could then reach the key by a bare glob).
CD_STATE = re.compile(r"(?:^|[\s;&|(])(?:cd|pushd)\s+(?:-\w+\s+)*(?:\S*/)?(state|approvals)/?(?=[\s;&|)]|$)")
# The runner/state folder itself as an operand, and the programs that would read or copy every file in it.
STATE_DIR = re.compile(r"runner/state/?(?=[\s;&|)]|$)")
RECURSIVE_READERS = re.compile(r"(?:^|[\s;&|(/])(?:[ef]?grep|rg|ag|ack|cp|rsync|tar|gtar|bsdtar|zip|ditto|find|scp|"
                               r"cpio|pax|xargs)(?=\s|$)")
# A path into the approvals folder, or the runner/state folder itself (replacing it replaces the key and approvals).
APPROVALS_TARGET = re.compile(r"(?:^|[\s/=])approvals(?=/|[\s;&|)]|$)|runner/state/?(?=[\s;&|)]|$)")
APPROVALS_FILE_PATH = re.compile(r"(^|/)state/approvals(/|$)")
# Programs that only read or list what they name and never write a file they are given. When a line names an approvals
# path, every simple command on it must be one of these (a pipe into xargs rm, a for loop or a variable could carry
# the path to a writer).
APPROVAL_READERS = {"cat", "ls", "head", "tail", "jq", "grep", "egrep", "fgrep", "rg", "wc", "stat", "shasum",
                    "sha256sum", "md5", "diff", "cmp", "echo", "printf", "test", "[", "realpath", "readlink",
                    "basename", "dirname", "du", "find", "cut", "nl"}
FIND_WRITES = re.compile(r"^-(delete|exec|execdir|ok|okdir|fprint|fprint0|fprintf|fls)$")
# ---- 2026-11-03 security review of the approval path ----
# The compiled Touch ID prompt (qa/app/.touchid, .touchid.build while it builds): only the app server writes it. A line
# naming it where a command is not a plain reader is denied (a copy of /usr/bin/true there approves without a finger).
HELPER_BIN = re.compile(r"(?:^|[\s/=])\.touchid(?:\.build)?(?![\w.-])")
HELPER_REASON = ("writes the Touch ID prompt the app runs (qa/app/.touchid): only the app server builds it from "
                 "touchid.swift; a replaced prompt would approve tickets without the person")
# The File ticket route: only the person presses it, in the app (one press plus Touch ID); never curl or code.
FILE_ROUTE = re.compile(r"api/tickets/file(?![\w/-])")
FILE_ROUTE_REASON = ("calls File ticket (POST /api/tickets/file): only the person presses it in the app, then confirms "
                     "with Touch ID")
# 2026-11-03: Post comment, the app's one ClickUp write (the results comment of a test), and the module that posts it:
# only the person's press in the app, then Touch ID; never curl, code or a connector. Review (same day): any path below
# api/results/comment/ counts (Post comment is the only one), so comment/$P, comment/{post,} or curl's comment/pos[t-t]
# count too; the route rules also read the line with URL dot segments dropped (_route_text); and the poster counts as
# an attribute too (serve.clickup_post or views.clickup_post reach it with no prompt at all).
COMMENT_ROUTE = re.compile(r"api/results/comment/(?=[^\s;&|)<>])")
COMMENT_ROUTE_REASON = ("calls Post comment (POST /api/results/comment/post): only the person presses it in the app, "
                        "then confirms with Touch ID")
POSTER = re.compile(r"(?<![\w-])clickup_post(?:\.py)?(?![\w-])")
POSTER_REASON = ("runs the app's ClickUp comment poster (qa/app/clickup_post.py): only the app server posts, after the "
                 "person's Post comment press and Touch ID")
# Review (same day): ClickUp's own comment endpoints over REST (curl, wget or code with a token) skip the app and Touch
# ID entirely; like the ClickUp comment tools (CLICKUP_COMMENT), they are denied to every line that is not a plain read.
CLICKUP_REST_COMMENT = re.compile(r"api\.clickup\.com(?=[\s\S]*?/comments?(?![\w-]))|"
                                  r"/comments?(?![\w-])(?=[\s\S]*?api\.clickup\.com)")
CLICKUP_REST_REASON = ("calls ClickUp's comment API directly: ClickUp comments are drafted locally, and only the app "
                       "server posts one, after the person's Post comment press and Touch ID")
APP_ROUTES = ((FILE_ROUTE, FILE_ROUTE_REASON), (COMMENT_ROUTE, COMMENT_ROUTE_REASON), (POSTER, POSTER_REASON),
              (CLICKUP_REST_COMMENT, CLICKUP_REST_REASON))
# "/./" and "/<segment>/../": curl and browsers drop them before they send a path (/api/tickets/./file is sent as
# /api/tickets/file)
DOT_SEGMENT = re.compile(r"/\.(?=/)|/[^/\s]+/\.\.(?=/)")
# A find that deletes or runs commands, or a recursive rm, over a folder that holds runner/state (an approval's .used
# marker could go, and the approval would then file its ticket again).
STATE_SWEEP_REASON = ("deletes or runs commands over a folder that holds the approvals (runner/state): a removed "
                      ".used marker would let an approval file its ticket twice")
# JS that an act.py read or a js: readback runs in the app's page: read-only expressions pass; anything that clicks,
# submits, sends, sets a value or builds code asks (a .click() on Next files a Swiss Army Knife ticket with no prompt).
# Read on the code with its string and regex literals emptied (_js_clean), so CSS like [role=dialog] in a querySelector
# is not an assignment. 2026-11-16: a lexer replaced the string regex JS_STRING, which took the quote in /*'*/ or /'/
# for the start of a string and so hid the code after it.
JS_REGEX_AFTER_WORD = {"case", "delete", "do", "else", "extends", "in", "instanceof", "new", "return", "throw", "typeof",
                       "void"}
JS_UNCLEAR_AFTER_WORD = {"await", "of", "yield"}  # a keyword or a name: a / after one may start a regex or divide
JS_CONTROL_WORDS = {"if", "while", "for", "with"}  # a / right after the ) of their ( starts a regex
JS_PUNCT_BEFORE_REGEX = set("(,=:[!&|?{};+-*%<>~^/")
JS_BOM = chr(0xFEFF)  # JS whitespace that Python's isspace() does not count
JS_STRING_KEY = re.compile(r"\[\s*(['\"])((?:\\.|(?!\1)[^\\\n])*)\1\s*\]")
# The global object and its aliases: a string key on them stays a bracket (window['x'] asks in JS_ACTION).
JS_GLOBAL_ALIASES = {"window", "document", "self", "top", "parent", "frames", "globalThis", "this", "Plugins"}
JS_LINE_ENDS = "\n\r" + chr(0x2028) + chr(0x2029)
# 2026-11-17 (the person's OK, "Yes, add them"): eleven page calls the list missed now ask: .constructor (reaches
# Function: code from a string), location.replace, new Audio/Image/Worker/SharedWorker (and other constructors that load
# a URL), document.write, indexedDB.deleteDatabase, a bare open() or postMessage, .onclick() and other on<event>
# handlers, form.reset(), serviceWorker.register(); with their close relatives (set*, delete*, remove*, write*, load,
# unregister*) and the bracket spellings: a member name that is only a string (x['constructor'], x['con' + 'structor'];
# member brackets are marked @[ by _js_clean; a key computed by code, as in the chart read's
# el[Object.keys(el).find(...)], is not) and self/top/parent/frames/this[...] like window[...].
JS_ACTION = re.compile(
    # names that act wherever they stand, called or only referenced ((0, b.click)(), [b].forEach(..., x.click))
    r"\.\s*(?:click|submit|requestSubmit|dispatchEvent|setAttribute\w*|removeAttribute\w*|setItem|removeItem|"
    r"execCommand|sendBeacon|postMessage|pushState|replaceState|nativePromise|nativeCallback|toNative)\b"
    # .constructor reaches Function (its .name is a read); an on<event> handler acts when called or handed on
    r"|\.\s*constructor\b(?!\s*\.\s*name\b)|\.\s*on[a-z]+\s*\("
    r"|\.\s*on(?:click|dblclick|submit|reset|change|input|keydown|keyup|keypress|mousedown|mouseup|pointerdown|"
    r"pointerup|touchstart|touchend|load|error|message|focus|blur)\s*[),\]}]"
    # names that act when called
    r"|\.\s*(?:focus|blur|select|toggle\w*|clear|set\w*|delete\w*|add|remove\w*|append\w*|prepend\w*|insert\w*|"
    r"send\w*|open|close|assign|reload|replace(?:With|Children|Child)|go|back|forward|call|apply|bind|define\w*|"
    r"write\w*|reset|load|register\w*|unregister\w*)\s*[(`]"
    r"|\blocation\s*\.\s*replace\s*\("
    r"|\b(?:fetch|eval|Function|setTimeout|setInterval|requestAnimationFrame|queueMicrotask|import|XMLHttpRequest|"
    r"WebSocket|EventSource|Reflect|Proxy|CapacitorHttp|postMessage)\b"
    r"|(?<![.\w$])open\b|\b(?:window|self|top|parent|frames|this)\s*\.\s*open\b"
    r"|\bnew\s+(?:\w*Event|Audio|Image|Worker|SharedWorker|FontFace|Notification|BroadcastChannel|RTCPeerConnection)\b"
    r"|Capacitor\s*\.\s*Plugins\s*\.\s*(?!Preferences\s*\.\s*(?:get|keys)\s*\()\w"
    r"|(?<![=!<>])=(?![=>])|\+\+|--|\]\s*[(`]|\bglobalThis\b"
    r"|\b(?:window|document|self|top|parent|frames|this|Plugins)\s*@?\[|@\[[\s(]*''(?:[\s)]*\+[\s(]*'')*[\s)]*\]")
# The one computed read the skills use: run.py's page expression, printed by run.py itself (sak-submit's build re-pin).
ACT_JS_FROM_RUN = re.compile(r"^\$\(python3 -I -c import sys; sys\.path\.insert\(0, qa/runner\); import run; "
                             r"print\(run\.[A-Z_]+\)\)$")
ACT_JS_REASON = ("act.py JS that may act in the app's page (click, submit, send, set a value, build code): the "
                 "Swiss Army Knife and other cloud writes go only through approved taps")
# act.py's options that take a value (argparse, allow_abbrev=False): a read's expression is not among their values; and
# its flags.
ACT_VALUE_OPTIONS = {"--expect", "--timeout", "--find-timeout", "--text", "--text-from", "--secret", "--nth", "--file",
                     "--case-file"}
ACT_FLAG_OPTIONS = {"--double", "--until-offline", "--approved", "--check", "-h", "--help"}
# 2026-11-16: adb push of plain test files into a QA folder on the tablet passes; see _test_file_push.
PUSH_SOURCE = re.compile(r"tests/[A-Za-z0-9][A-Za-z0-9._-]*/files/[A-Za-z0-9][A-Za-z0-9._-]*")
PUSH_DEST = re.compile(r"/sdcard/Download/(?:QA-[A-Za-z0-9][A-Za-z0-9._-]*|WingtraQA)(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?/?")


def _now():
    """The gate's clock (UTC); the tests replace it."""
    return datetime.datetime.now(datetime.timezone.utc)


def _flat(text, join=False):
    """Tokens with quotes, brackets and commas made spaces, so ['adb','-s',S,'shell','input'] reads like a shell line.
    join=True (2026-11-16): quotes and backslashes are dropped instead, as the shell joins a word (p''ush, p\\ush and
    'pu'sh are push)."""
    text = re.sub(r"[\[\]\(\)\{\},]", " ", text)
    return re.sub(r"['\"\\]", "", text) if join else re.sub(r"['\"]", " ", text)


def _adb_command_at(words):
    """2026-11-16 (the person's OK, "Yes, fix them"): the index of the adb command in words (an adb call's arguments
    with -s <serial> taken out), past adb's global options (-d, -e, -t <id>, -H <host>, -P <port>, -L <socket>, ...);
    None when an option or word the gate does not read stands before it (-a, nodaemon, server, an unknown -x). The
    rules match the command at its start, so adb -s X -t 1 push hid the push from them."""
    i = 0
    while i < len(words):
        w = words[i]
        if w in ("server", "nodaemon", "fork-server"):
            return None
        if not w.startswith("-") or re.fullmatch(r"--version|-?-help", w):
            return i
        if w in ADB_PLAIN_OPTIONS or re.fullmatch(r"-t[0-9]+|-[sHP]\S+", w):
            i += 1
        elif w == "-t":
            if i + 1 >= len(words) or not re.fullmatch(r"[0-9]+", words[i + 1]):
                return None
            i += 2
        elif w in ADB_VALUE_OPTIONS:
            i += 2
        else:
            return None
    return i


def _adb_segments(cmd):
    """Each adb invocation as (args_text, has_serial). 2026-11-16: read twice, with quotes as word breaks (every
    mention, as before) and with quotes and backslashes dropped as the shell joins words (p''ush is push); a
    backslash-newline joins the lines first, as the shell does (adb -s X \\<newline>push hid the push)."""
    out = []
    joined = re.sub(r"\\\r?\n", " ", cmd)
    for flat in (_flat(joined), _flat(joined, join=True)):
        for seg in SEPARATORS.split(flat):
            for m in re.finditer(r"(?:^|\s)(?:\S*/)?adb\s+(.*)$", seg):
                args = m.group(1)
                has_serial = bool(re.search(r"(^|\s)-s\s+\S+", args)) or "ANDROID_SERIAL=" in cmd or re.search(r"(^|\s)-[de](\s|$)", args)
                args_wo = re.sub(r"(^|\s)-s\s+\S+", " ", args).strip()
                if (args_wo, bool(has_serial)) not in out:
                    out.append((args_wo, bool(has_serial)))
    return out


def _sub_end(text, i):
    """Index past the ` or ) that closes the substitution opening at i (`, $(, <( or >(); quotes, escapes and nested
    ( ) inside are skipped. ValueError when it is not closed."""
    n = len(text)
    if text[i] == "`":
        j = i + 1
        while j < n:
            if text[j] == "`":
                return j + 1
            j += 2 if text[j] == "\\" else 1
        raise ValueError("unclosed `")
    depth, j = 0, i + 1
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            k = text.find("'", j + 1)
            if k < 0:
                raise ValueError("unclosed '")
            j = k + 1
            continue
        if c == '"':
            j = _dq_end(text, j + 1, [])
            continue
        if c == "`":
            j = _sub_end(text, j)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError("unclosed (")


def _dq_end(text, i, subs):
    """Index past the " that closes the double-quoted string whose text starts at i; the bodies of its $(...) and `...`
    go to subs."""
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
        elif c == '"':
            return i + 1
        elif c == "`" or text.startswith("$(", i):
            j = _sub_end(text, i)
            subs.append(text[i + (1 if c == "`" else 2):j - 1])
            i = j
        else:
            i += 1
    raise ValueError('unclosed "')


def _expansions(body):
    """The $(...) and `...` bodies of an unquoted heredoc: the shell runs them before the reader sees the text."""
    subs, j = [], 0
    while j < len(body):
        if body[j] == "\\":
            j += 2
        elif body[j] == "`" or body.startswith("$(", j):
            e = _sub_end(body, j)
            subs.append(body[j + (1 if body[j] == "`" else 2):e - 1])
            j = e
        else:
            j += 1
    return subs


def _sh_scan(text, depth=0, glob_mark=False):
    """Each simple command the shell runs from text, as its words with quotes removed (a substitution leaves SUBST in
    its word). Commands split at ; & | ( ) and newlines, not inside quotes; $(...), `...`, <(...) and >(...) bodies,
    also inside double quotes and unquoted heredocs, are commands of their own; # starts a comment only at the start
    of a word; < and > end a word. A quoted heredoc body is data unless a shell or ssh on its line reads it (a body fed
    to python/node/perl/ruby is read by the code scan). ValueError when a quote or substitution is not closed, or
    substitutions nest deeper than 4. With glob_mark (2026-11-04), a * ? [ or { outside quotes is kept as its
    GLOB_MARKS character, so a caller can tell a glob the shell expands from a quoted one."""
    if depth > 4:
        raise ValueError("substitutions nest too deep")
    out, cmd, word, heredocs, line0 = [], [], None, [], 0
    i, n = 0, len(text)

    def end_word():
        nonlocal word
        if word is not None:
            cmd.append(word)
        word = None

    def end_cmd():
        end_word()
        if cmd:
            out.append(list(cmd))
        cmd.clear()

    def run(body):
        out.extend(_sh_scan(body, depth + 1, glob_mark))

    while i < n:
        c, nxt = text[i], text[i + 1:i + 2]
        if c in " \t\r":
            end_word()
            i += 1
        elif c == "\n":
            end_cmd()
            line, i = text[line0:i], i + 1
            for delim, quoted in heredocs:
                m = re.compile(r"^[ \t]*" + re.escape(delim) + r"[ \t]*$", re.M).search(text, i)
                if not m:  # 2026-11-04: an unclosed heredoc (or a << shift read as one) leaves the line unreadable
                    raise ValueError("heredoc without its end line")
                body, i = text[i:m.start()], m.end()
                if SH_READER.search(line):
                    run(body)
                elif not quoted:
                    for b in _expansions(body):
                        run(b)
            heredocs, line0 = [], i
        elif c == "#" and word is None:
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c == "\\":
            if nxt != "\n":
                word = (word or "") + nxt
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise ValueError("unclosed '")
            word, i = (word or "") + text[i + 1:j], j + 1
        elif c == "$" and nxt == "'":
            j = i + 2
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise ValueError("unclosed $'")
            word, i = (word or "") + text[i + 2:j], j + 1
        elif c == '"':
            subs = []
            j = _dq_end(text, i + 1, subs)
            for b in subs:
                run(b)
            word, i = (word or "") + re.sub(r'\\([\\"$`])', r"\1", text[i + 1:j - 1]), j
        elif c == "`" or (c in "$<>" and nxt == "("):
            j = _sub_end(text, i)
            run(text[i + (1 if c == "`" else 2):j - 1])
            word, i = (word or "") + SUBST_MARK, j
        elif text.startswith("<<<", i):
            end_word()
            word, i = "<<<", i + 3  # a here-string: the next word is data
        elif text.startswith("<<", i):
            end_word()
            j = i + 2 + (text[i + 2:i + 3] == "-")
            while j < n and text[j] in " \t":
                j += 1
            delim, quoted = "", False
            while j < n and text[j] not in " \t\n;&|()<>":
                if text[j] in "'\"":
                    k = text.find(text[j], j + 1)
                    if k < 0:
                        raise ValueError("unclosed heredoc word")
                    delim, quoted, j = delim + text[j + 1:k], True, k + 1
                elif text[j] == "\\":
                    delim, quoted, j = delim + text[j + 1:j + 2], True, j + 2
                else:
                    delim, j = delim + text[j], j + 1
            if delim:
                heredocs.append((delim, quoted))
                cmd.append(HEREDOC_MARK)  # the command reads this heredoc (an interpreter's program, say)
            i = j
        elif c in "<>":
            if word is not None and not word.isdigit() and not word.endswith(("<", ">", "&")):
                end_word()
            word, i = (word or "") + c, i + 1
        elif c == "&" and (nxt == ">" or (word or "").endswith(("<", ">"))):
            if nxt == ">":
                end_word()
            word, i = (word or "") + c, i + 1  # &> and the & of 2>&1 or >&2 redirect, they do not end a command
        elif c == "|" and (word or "").endswith(">"):
            word, i = word + c, i + 1  # >| overwrites a file
        elif c in ";&|()":
            end_cmd()
            i += 1
        else:
            word, i = (word or "") + (GLOB_MARKS.get(c, c) if glob_mark else c), i + 1
    end_cmd()
    return out


def _no_redirects(words):
    """words without their redirections: an operator with its file glued on (>out, 2>&1) or as the next word (> out)."""
    out, skip = [], False
    for w in words:
        if skip:
            skip = False
            continue
        m = REDIRECT_WORD.match(w)
        if m:
            skip = m.end() == len(w)
            continue
        out.append(w)
    return out


def _old_reading(text, top):
    """The pre-2026-11-02 reading of text (every mention of adb is a call), with ANDROID_SERIAL set anywhere on the
    whole line counting as before. A mention without words after it has arguments the gate cannot see ("_"), not none
    (2026-11-02 review: echo adb | xargs -I{} {} shell ls)."""
    return [(a or "_", has or "ANDROID_SERIAL=" in top) for a, has in _adb_segments(text)]


def _words_reading(words, top):
    """The old reading of a command's unquoted words; an = glued to adb (VAR=adb ..., --opt=adb ...) parts words."""
    return _old_reading(" ".join(words).replace("=", " "), top)


def _literal_line(node):
    """The command line a call's command argument spells: a string, or a list or tuple of strings (joined as shell
    words). None when any part is computed at run time (a name, an f-string field, a call)."""
    if isinstance(node, (ast.List, ast.Tuple)):
        parts = [_literal_line(e) for e in node.elts]
        return None if None in parts else shlex.join(parts)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_line(node.left), _literal_line(node.right)
        lists = isinstance(node.left, (ast.List, ast.Tuple)) or isinstance(node.right, (ast.List, ast.Tuple))
        return None if left is None or right is None else (left + " " + right if lists else left + right)
    return None


def _code_adb_calls(code, top, depth):
    """adb calls of inline code: the command line of each subprocess/os.system-style call, read like a shell line.
    Code that is not Python keeps the old reading of the code. Code that runs a command it computes at run time (it may
    come from argv or stdin), or reaches a run function another way (renamed on import, assigned or passed on, looked up
    by name, a call through a call, eval/exec/compile), keeps the old reading of the whole line (2026-11-02 review)."""
    tree, bare = None, code.rstrip("; &|\t\n")
    for src in (code, bare, bare[:-1] if bare.endswith(")") else bare):  # _inline_code keeps a ; or ) glued to the quote
        try:
            tree = ast.parse(textwrap.dedent(src))
            break
        except (SyntaxError, ValueError):
            pass
    if tree is None:
        return _old_reading(code, top)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.asname and CODE_RUN_REF.match(a.name) for a in node.names):
            return _old_reading(top, top)  # from os import system as s
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CODE_RUN_REF.match(node.value):
            return _old_reading(top, top)  # getattr(os, 'system')
        if isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in called and \
                CODE_RUN_REF.match(node.attr if isinstance(node, ast.Attribute) else node.id):
            return _old_reading(top, top)  # f = os.system; map(os.system, ...); partial(subprocess.run, ...)
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, (ast.Name, ast.Attribute)) or \
                (isinstance(f, ast.Name) and f.id in ("eval", "exec", "compile")):
            return _old_reading(top, top)  # getattr(os, 'system')(...), eval("...")
        name = f.attr if isinstance(f, ast.Attribute) else f.id
        if not CODE_RUN_CALL.match(name):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        arg = node.args[0] if node.args else kw.get("args", kw.get("cmd", kw.get("command")))
        if arg is None and None not in kw:
            continue  # no command given
        line = _literal_line(arg) if arg is not None else None
        if line is not None and node.args and not re.search(r"\s", line):
            rest = [_literal_line(a) for a in node.args[1:]]  # a lone program, its arguments after it (os.execvp)
            line = None if None in rest else " ".join([line] + rest)
        if line is None:
            return _old_reading(top, top)
        out += _adb_calls(line, top, depth + 1)
    return out


def _adb_serial(args):
    """True when adb's own options, before its command, pick one device: -s SERIAL (or -sSERIAL), -d or -e. A -s after
    the command (adb shell ls -s) is the command's option."""
    j = 0
    while j < len(args) and args[j].startswith("-"):
        a = args[j]
        if a in ("-d", "-e") or (a == "-s" and j + 1 < len(args)) or (a.startswith("-s") and len(a) > 2):
            return True
        j += 2 if a in ("-t", "-H", "-P", "-L") else 1
    return False


def _shell_code(args):
    """The command string of sh -c and the like: the first operand after the options when one of them holds c (bash -c
    'x', -c -- 'x', -ce 'x', -c -x 'x', -o pipefail -c 'x'); None without -c."""
    c, j = False, 0
    while j < len(args):
        a = args[j]
        if a == "--":
            j += 1
            break
        if a in SH_VALUE_OPTS:
            j += 2
            continue
        if len(a) > 1 and a[0] in "-+":
            c = c or (a[0] == "-" and not a.startswith("--") and "c" in a[1:])
            j += 1
            continue
        break
    return args[j] if c and j < len(args) else None


def _interp_stdin(args):
    """True when python/node/perl/ruby read their program from stdin: no -c/-e code, no -m module, and no script
    operand, or the operand is -, /dev/stdin or a <(...)."""
    j = 0
    while j < len(args):
        a = args[j]
        if CODE_FLAG.match(a) or (a.startswith("-m") and not a.startswith("--")):
            return False
        if a == "--":
            j += 1
            break
        if a in INTERP_VALUE_OPTS:
            j += 2
            continue
        if a.startswith("-") and a != "-":
            j += 1
            continue
        break
    op = args[j] if j < len(args) else None
    return op is None or op in ("-", "/dev/stdin", "/dev/fd/0") or SUBST_MARK in op


def _text_only(prog, args):
    """True when prog only reads or prints args (TEXT_PROGRAMS), so adb among them is text; not when adb stands in or
    after one of its options whose value is a program, or in awk code that can run a command."""
    if prog not in TEXT_PROGRAMS:
        return False
    if prog in AWKS and any(re.search(r"system|getline|\|", a) for a in args):
        return False
    opts = TEXT_PROGRAMS[prog]
    for j, a in enumerate(args):
        if ADB_WORD.search(a) and ((j and args[j - 1] in opts) or a.split("=", 1)[0] in opts
                                   or any(len(o) == 2 and a.startswith(o) for o in opts)):
            return False
    return True


def _adb_calls(cmd, top=None, depth=0):
    """Each adb call the line runs as a program, as (args_text, has_serial) like _adb_segments: the first word of a
    simple command (after shell keywords, '(', env assignments, redirections and the WRAPPERS such as sudo, xargs, exec,
    time and nohup), inside sh -c, eval, find -exec, ssh and watch, and the command of a subprocess call in inline
    code. A program word computed at run time ($VAR, $(...)) counts as adb when the line names adb anywhere. A line the
    gate cannot read, or nesting deeper than 3, keeps the old reading: every mention counts.
    2026-11-02 review: a mention is text only as an argument of a TEXT_PROGRAMS program. It keeps the old reading in the
    arguments of any other program, in an env assignment or a wrapper's option, in ssh's options, and on the whole line
    when a shell, an interpreter or a STDIN_RUNNERS program runs text the gate cannot read (a script, stdin, a pipe, a
    here-string, <(...), xargs)."""
    top = cmd if top is None else top
    if depth > 3:
        return _old_reading(cmd, top)
    try:
        cmds = _sh_scan(cmd)
    except (ValueError, IndexError, RecursionError):
        return _old_reading(cmd, top)
    out = []
    unseen = bool(ADB_WORD.search(top))  # only then can text the gate cannot read hold an adb call
    for words in cmds:
        fed = HEREDOC_MARK in words
        words = [w for w in _no_redirects(words) if w != HEREDOC_MARK]
        prog, args, wrappers = _command(words)
        if not prog and "xargs" in wrappers and unseen:
            out += _old_reading(top, top)  # xargs env, xargs sudo: the program itself comes from stdin
        if not prog or ("command" in wrappers and re.search(r"(^|\s)(\S*/)?command\s+-[vV](\s|$)", " ".join(words))):
            continue  # nothing runs, or command -v only looks the program up
        k = next((j for j, w in enumerate(words) if os.path.basename(w.lstrip("(")) == prog), len(words))
        if any(ADB_WORD.search(w) for w in words[:k]):
            out += _words_reading(words, top)  # GIT_SSH_COMMAND='adb ...' git fetch, a wrapper's option
        prog = prog[1:] if prog.startswith("=") else prog  # zsh: =adb is the path of adb
        computed = prog.startswith("$") or SUBST_MARK in prog
        if prog == "adb" or (computed and ADB_WORD.search(top)):
            more = ["_"] if "xargs" in wrappers else []  # xargs adds more
            if computed and not args + more:
                out += _old_reading(top, top)  # $CMD: the whole command line is in the variable, the mentions decide
                continue
            text = " ".join(args + more).replace(SUBST_MARK, "_")
            out.append((SERIAL_ARG.sub(" ", text).strip(), _adb_serial(args + more) or "ANDROID_SERIAL=" in top))
            continue
        # what the command runs itself; the words are unquoted already, so not _inner (it strips quotes off the ends)
        lines, text = [], True
        if prog in SH_PROGRAMS:
            code = _shell_code(args) if prog not in ("source", ".") else None
            if code is None:
                if unseen:
                    out += _old_reading(top, top)  # a script file, stdin, a pipe, a here-string or <(...)
                continue
            lines = [code]
        elif prog in STDIN_RUNNERS:
            if unseen:
                out += _old_reading(top, top)
            continue
        elif INTERP_PROG.match(prog) and (any(CODE_FLAG.match(a) for a in args) or _interp_stdin(args)):
            # its -c/-e code or its heredoc: the code scan below; a program from a pipe, a here-string or <(...): unseen
            if unseen and not fed and not any(CODE_FLAG.match(a) for a in args):
                out += _old_reading(top, top)
        elif prog == "eval":
            lines = [" ".join(args)]
        elif prog == "find":
            lines = _find_execs(args)  # the rest of find's arguments are text
        elif prog in LINE_RUNNERS:
            n = _after_wrapper(args, 0, *LINE_RUNNERS[prog])
            if any(ADB_WORD.search(a) for a in args[:n]):
                out += _words_reading([prog] + args[:n], top)  # ssh -o ProxyCommand='adb ...'
            lines = [" ".join(args[n:])] if n < len(args) else []
        elif re.search(r"\s", prog):
            lines = [" ".join([prog] + args)]  # a quoted word with spaces as the program: env -S splits it
        else:
            text = _text_only(prog, args)
            if not text and prog in AWKS and unseen:
                out += _old_reading(top, top)  # awk code that runs commands, from its input too
            elif not text and any(ADB_WORD.search(a) for a in [prog] + args):  # {adb,} and noglob adb too
                out += _words_reading([prog] + args, top)  # arch -arm64 adb ..., script -q /dev/null adb ...
        if "xargs" in wrappers and unseen and not (text and prog in TEXT_PROGRAMS):
            out += _old_reading(top, top)  # xargs adds words from stdin to a command that may run them
        for line in lines:
            out += _adb_calls(line, top, depth + 1)
    for code in _code_bodies(cmd):
        out += _code_adb_calls(code, top, depth)
    return out


def _runs_adb_unserialed(cmd):
    """True when the line runs adb as a program without -s/-d/-e/ANDROID_SERIAL, for more than a device-free read (adb
    alone only prints its help)."""
    try:
        calls = _adb_calls(cmd)
    except Exception:  # noqa: BLE001 - a scan the gate cannot finish keeps the old reading
        calls = _old_reading(cmd, cmd)
    return any(not has and a.strip() and not ADB_NO_DEVICE.match(" ".join(a.split())) for a, has in calls)


def _segments(cmd):
    """Each simple command of a shell line as a token list (quotes kept together where shlex can parse it).
    A $(...) or `...` substitution is a command of its own and leaves the word _ in the outer one."""
    texts = []
    m = SUBST.search(cmd)
    while m:
        texts.append(m.group(1) if m.group(1) is not None else m.group(2))
        cmd = cmd[:m.start()] + "_" + cmd[m.end():]
        m = SUBST.search(cmd)
    out = []
    for seg in SPLIT.split("\n".join(texts + [cmd])):
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:
            toks = seg.split()
        if toks:
            out.append(toks)
    return out


def _after_wrapper(toks, i, value_opts, operands):
    """Index of the wrapped command: past the wrapper's options, their values and its leading operands."""
    while i < len(toks) and toks[i].startswith("-"):
        i += 1
        if toks[i - 1] == "--":
            break
        if toks[i - 1] in value_opts:
            i += 1
    return i + operands


def _command(toks):
    """(program basename, args, wrappers) after shell keywords, '(', env assignments and wrappers like env/xargs."""
    i, wrappers = 0, []
    while i < len(toks):
        t = toks[i].lstrip("(")
        name = os.path.basename(t)
        if not t or t in SHELL_WORDS:
            i += 1
        elif t == "function":
            i += 2
        elif re.match(r"^[A-Za-z_]\w*=", t):
            i += 1
        elif name in WRAPPERS:
            wrappers.append(name)
            i = _after_wrapper(toks, i + 1, *WRAPPERS[name])
        else:
            return name, [a for a in toks[i + 1:] if not REDIRECT.match(a)], wrappers
    return None, [], wrappers


def _git(args, stdin=False):
    """Decision for one git invocation's arguments (after the program name); stdin: xargs appends more."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        name = args[i].split("=", 1)[0]
        i += 2 if name in GIT_GLOBAL_WITH_VALUE and "=" not in args[i] else 1
    if i >= len(args):
        return ("ask", "xargs supplies the git subcommand from stdin, which the gate cannot read") if stdin \
            else ("pass", "")
    sub, rest = args[i].strip("'\""), args[i + 1:] + (["_"] if stdin else [])
    if sub in GIT_WRITES:
        return "deny", f"git {sub} changes a repository: the clones are read-only (G1)"
    if sub == "worktree":
        if rest[:1] == ["list"]:
            return "pass", ""
        return ("deny" if rest[:1] == ["add"] else "ask"), f"git worktree {' '.join(rest[:1])} changes a repository"
    if sub == "branch":
        if any(GIT_BRANCH_WRITE.match(a) for a in rest):
            return "deny", "git branch with a write option changes a repository (G1)"
        positional = [a for a in rest if not a.startswith("-")]
        if positional and not any(GIT_BRANCH_LIST.match(a) for a in rest):
            return "deny", "git branch <name> creates a branch (G1)"
        return "pass", ""
    if sub == "tag":
        positional = [a for a in rest if not a.startswith("-")]
        if any(a.startswith("-") and not GIT_TAG_LIST.match(a) for a in rest) or (
                positional and not any(GIT_TAG_LIST.match(a) for a in rest)):
            return "deny", "git tag with a name or write option creates or deletes a tag (G1)"
        return "pass", ""
    if sub == "remote" and (not rest or rest[0] in ("-v", "--verbose", "get-url", "show")):
        return "pass", ""
    if sub == "config" and rest and rest[0] in ("--get", "--get-all", "--get-regexp", "--list", "-l"):
        return "pass", ""
    if sub in GIT_READS:
        return "pass", ""
    return "ask", f"git {sub} is not a known read-only git command"


def _find_execs(args):
    """The command lines find runs with -exec, -execdir, -ok or -okdir."""
    out = []
    for j, a in enumerate(args):
        if a in FIND_EXEC:
            body = []
            for b in args[j + 1:]:
                if b in (";", "+", "\\;", "\\"):
                    break
                body.append(b)
            out.append(shlex.join(body))
    return out


def _find_writes_clone(args):
    """find over a clone path that deletes, or runs a file-changing command on, what it finds (G1)."""
    if not any(CLONE_PATH.search(a) for a in args):
        return False
    if "-delete" in args:
        return True
    for body in _find_execs(args):
        prog, bargs, _ = _command(shlex.split(body))
        if prog in ("rm", "mv", "truncate", "tee", "touch") or (prog in ("cp", "ln") and "{}" in "".join(bargs[-1:])) \
                or (prog == "sed" and any(a.startswith(("-i", "--in-place")) for a in bargs)):
            return True
    return False


def _inner(prog, args):
    """Command lines a simple command runs itself: sh -c bodies, eval, find -exec bodies. A body the line split
    cut in pieces is taken with what follows it and without its opening quote."""
    if prog in SHELLS:
        return [" ".join(args[j + 1:]).strip("'\"") for j, a in enumerate(args[:-1])
                if a == "-c" or (a.startswith("-") and a.endswith("c") and not a.startswith("--"))]
    if prog == "eval":
        return [" ".join(args).strip("'\"")]
    return _find_execs(args) if prog == "find" else []


def _abs_vars(cmd):
    """TMPDIR, HOME and the variables the line sets only to absolute paths, directly or through such a variable.
    Heredoc bodies are left out: they never set the outer shell's variables."""
    vals = {}
    for m in ASSIGN.finditer(HEREDOC_BODY.sub(r"\1", cmd)):
        vals.setdefault(m.group(1), []).append(m.group(3) or ("/" if m.group(2) else None))
    absvars = {"TMPDIR", "HOME"} - set(vals)
    grew = True
    while grew:
        new = {n for n, vs in vals.items() if n not in absvars and all(v == "/" or v in absvars for v in vs)}
        absvars |= new
        grew = bool(new)
    return absvars


def _without_data_heredocs(text):
    """text without the bodies of quoted heredocs that no shell reads: they are data (no expansion, no commands)."""
    def keep(m):
        line = m.string[m.string.rfind("\n", 0, m.start()) + 1:m.start()]
        shell = re.search(r"(^|[\s;&|(/])(bash|sh|zsh|dash)\b", line)
        return m.group(0) if not m.group(2) or shell else m.group(1)
    return HEREDOC_BODY.sub(keep, text)


def _relative(word, absvars):
    """True when a write target may resolve under the current directory (not /, ~ or a variable set to one)."""
    m = re.match(r"\$\{?(\w+)", word)
    return m.group(1) not in absvars if m else not word.startswith(("/", "~"))


def _relative_write(text, absvars):
    """True when text redirects to, or runs rm/mv/cp/ln/tee/touch/truncate, sed -i or find -delete on, a
    relative path."""
    if any(_relative(m.group(1), absvars) for m in REDIRECT_TARGET.finditer(text)):
        return True
    for seg in WRITE_SPLIT.split(text):
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:
            toks = seg.split()
        prog, args, _ = _command(toks)
        ops = [a for a in args if a and not a.startswith("-")]
        if prog == "sed" and any(a.startswith(("-i", "--in-place")) for a in args):
            scripts = {args[j + 1] for j, a in enumerate(args[:-1]) if a in ("-e", "-f", "--expression", "--file")}
            ops = [o for o in ops if o not in scripts] if scripts else ops[1:]
        elif prog == "find" and ("-delete" in args or any(_command(shlex.split(b))[0] in WRITE_VERBS
                                                          for b in _find_execs(args))):
            ops = [a for a in args[:next((j for j, a in enumerate(args) if a[:1] in "-(!"), len(args))]]
        elif prog in ("cp", "ln"):
            ops = ops[-1:]
        elif prog not in WRITE_VERBS:
            continue
        if any(_relative(o, absvars) for o in ops):
            return True
    return False


def _inline_code(cmd):
    """The code arguments of python/node/perl/ruby -c/-e/--eval: each interpreter's options are read up to its
    first operand."""
    out = []
    for m in INTERPRETER.finditer(cmd):
        lex = shlex.shlex(cmd[m.end():], posix=True)
        lex.whitespace_split = True
        try:
            tok = lex.get_token()
            for _ in range(10):
                if not tok or not tok.startswith("-"):
                    break
                flag, tok = CODE_FLAG.match(tok), lex.get_token()
                if flag and flag.group(2):
                    out.append(flag.string[flag.end():])
                elif flag and tok and not tok.startswith("-"):
                    out.append(tok)
                    tok = lex.get_token()
        except ValueError:
            out.append(cmd[m.end():].split("\n", 1)[0])
    return out


def _unseen_write(cmd):
    """Writes the gate cannot read the target of: a relative shell write after cd into a clone, and git or gh
    run from inline python/node/perl/ruby code."""
    m = CD_CLONE.search(cmd)
    if m and _relative_write(_without_data_heredocs(cmd[m.end():]), _abs_vars(cmd)):
        return "ask", "a shell write after cd into a clone may change the clone (G1)"
    for code in _inline_code(cmd):
        git = CODE_GIT.search(code)
        if CODE_GH.search(code) or (git and CODE_GIT_WRITE.search(code, git.end())):
            return "ask", "inline interpreter code runs git or gh, which the gate cannot read (G1)"
    return "pass", ""


def _code_bodies(cmd):
    """Code an interpreter runs from this line: -c/-e arguments and heredoc bodies fed to python/node/perl/ruby."""
    out = list(_inline_code(cmd))
    for m in HEREDOC_BODY.finditer(cmd):
        line = cmd[cmd.rfind("\n", 0, m.start()) + 1:m.start()] + m.group(1)
        if INTERPRETER.search(line):
            out.append(m.group(0)[len(m.group(1)):])
    return out


def _act_call(cmd):
    """G2: the approval flag in any disguise. The plain form removes quotes and backslashes, as the shell joins a word."""
    plain = re.sub(r"['\"\\]", "", cmd)
    if ACT_CALL.search(plain):
        if APPROVED_PREFIX.search(plain):
            if re.search(r"(?:^|\s)QA_RUNNER=1\b", plain):
                return "deny", "write-list actions never run under the runner"
            m = re.search(r"act\.py\s+(\S+)\s+(\S+)\s+(\S+)", plain)
            what = f"{m.group(2)} {m.group(3)} on {m.group(1)}" if m else "a write-list action"
            return "ask", f"act.py write-list action: {what}"
        for m in ACT_VERB.finditer(plain):
            verb = m.group(2)
            if (verb in ACT_INPUT_VERBS or "$" in verb or "`" in verb) and re.search(r"[$`]", cmd):
                return "ask", "act.py input with a shell expansion the gate cannot read (it could hold --approved)"
    for code in _code_bodies(cmd):
        if ACT_REF.search(code) and CODE_RUNS.search(code):
            return "ask", "inline code runs act.py; the gate cannot read the options it passes"
    return "pass", ""


def _act_js(cmd):
    """2026-11-03 security review: the JS of an act.py read and of its js: readbacks runs in the app's page, where a
    .click() on Next files a Swiss Army Knife ticket with no prompt. Read-only expressions pass; one that may act
    (JS_ACTION, on the code with its strings emptied), and JS with $ or a backtick (computed at run time, or a template),
    other than run.py printing its own page expression, asks. Never skipped by the approval pass."""
    if "act.py" not in cmd:
        return "pass", ""
    text = _without_data_heredocs(cmd)
    for m in re.finditer(r"act\.py\b", text):
        rest = re.split(r"(?<!\\)\n", text[m.end():], 1)[0]  # this command line (a backslash-newline continues it)
        try:
            # 2026-11-16: ; & | split a word even when glued to it ("x"; adb ...), as the shell splits it
            lex = shlex.shlex(rest, posix=True, punctuation_chars=";&|")
            lex.whitespace_split, lex.commenters = True, ""
            toks = list(lex)
        except ValueError:
            if re.search(r"\bread\b|js:", rest):
                return "ask", ACT_JS_REASON + " (the gate cannot read its quoting)"
            continue
        end = next((i for i, t in enumerate(toks) if t in (";", "&&", "||", "|", "&", "|&", ";;")), len(toks))
        toks = toks[:end]
        # read's expression: every word after the verb that is not one of act.py's options or an option's value, and
        # every word after -- (2026-11-16: argparse takes -document...click() after -- as the expression; option values
        # such as --expect path:/x are not JS, and js: readbacks are read below)
        code, after_dd, skip = [], False, False
        for t in (toks[toks.index("read") + 1:] if "read" in toks else []):
            if skip:
                skip = False
            elif after_dd or not t.startswith("-"):
                code.append(t)
            elif t == "--":
                after_dd = True
            elif t in ACT_VALUE_OPTIONS:
                skip = True
            elif t not in ACT_FLAG_OPTIONS and t.split("=", 1)[0] not in ACT_VALUE_OPTIONS:
                code.append(t)  # not an option act.py knows: read it as JS too
        for i, t in enumerate(toks):
            v = toks[i + 1] if t == "--expect" and i + 1 < len(toks) else t[9:] if t.startswith("--expect=") else ""
            if v.startswith("js:"):
                code.append(v[3:])
        for js in code:
            if re.search(r"[$`]", js):
                if ACT_JS_FROM_RUN.match(re.sub(r"['\"]", "", js)):
                    continue
                return "ask", ACT_JS_REASON + " (JS computed at run time, or a template the gate cannot read)"
            clean = _js_clean(js)
            if clean is None:
                return "ask", ACT_JS_REASON + (" (a with statement, a backslash, # or @ outside a string, an unclosed "
                                               "string, comment or regex, or a / the gate cannot place as a regex or a "
                                               "division)")
            if JS_ACTION.search(clean):
                return "ask", ACT_JS_REASON
    return "pass", ""


def _js_clean(js):
    """2026-11-16 (the person's OK, "Yes, fix them"): the page JS with each string and regex literal replaced by '' and
    each comment by a space, read the way the browser reads it (sloppy script, as CDP Runtime.evaluate runs it); None
    when the gate cannot read it plainly: a with statement (with(document.body)click() is a click without a '.'), a
    backslash outside a string (\\u0063lick spells click; a backslash-newline is a line continuation of the shell), # or
    @ outside a string, an HTML-like comment (<!--, -->) or a #! line, an unclosed string, comment or regex, or a /
    that may start a regex or divide (after }, of, yield, await). A / starts a regex at the start, after an operator or
    ( , [ { ; : ?, after return, typeof and the like (not after a property name: x.return / 2 divides), and after the
    ) of if/while/for/with; elsewhere it divides. Review of the same day: comments are skipped as the browser skips
    them (a comment used to ask), and with asks."""
    if js.lstrip().startswith("#!"):
        return None
    out, i, n = [], 0, len(js)
    prev = ""   # the last token: "" (start), "punct:<c>", "word:<keyword>", "name", "value", ")", "ctl)" or "await)"
    opens = []  # what each open ( follows: "ctl" (if/while/for/with), "await" or ""
    while i < n:
        c = js[i]
        if c.isspace() or c == JS_BOM:
            out.append(" ")
            i += 1
        elif c in "'\"":
            j = i + 1
            while j < n and js[j] != c:
                if js[j] in "\n\r":
                    return None
                j += 2 if js[j] == "\\" else 1
            if j >= n:
                return None
            out.append("''")
            prev, i = "value", j + 1
        elif c == "\\" and i + 1 < n and js[i + 1] in JS_LINE_ENDS:
            out.append(" ")  # a backslash-newline the shell keeps (zsh drops it; a JS error either way)
            i += 2
        elif c in "`\\#@":
            return None
        elif js.startswith("/*", i):
            j = js.find("*/", i + 2)
            if j < 0:
                return None
            out.append(" ")
            i = j + 2
        elif js.startswith("//", i):
            j = i
            while j < n and js[j] not in JS_LINE_ENDS:
                j += 1
            out.append(" ")
            i = j
        elif c == "/":
            if prev in ("punct:}", "await)") or prev.startswith("word:") and prev[5:] in JS_UNCLEAR_AFTER_WORD:
                return None
            if prev in ("", "ctl)") or prev.startswith("punct:") and prev[6:] in JS_PUNCT_BEFORE_REGEX or \
                    prev.startswith("word:") and prev[5:] in JS_REGEX_AFTER_WORD:
                j, in_class = i + 1, False
                while j < n and (in_class or js[j] != "/"):
                    if js[j] in JS_LINE_ENDS:
                        return None
                    if js[j] == "\\":
                        j += 1
                    elif js[j] == "[":
                        in_class = True
                    elif js[j] == "]":
                        in_class = False
                    j += 1
                if j >= n:
                    return None
                j += 1
                while j < n and (js[j].isalnum() or js[j] == "_"):
                    j += 1
                out.append("''")
                prev, i = "value", j
            else:
                out.append("/")
                prev, i = "punct:/", i + 1
        elif c.isalnum() or c in "_$" or ord(c) > 127:
            j = i
            while j < n and (js[j].isalnum() or js[j] in "_$" or ord(js[j]) > 127 and not js[j].isspace()
                             and js[j] != JS_BOM):
                j += 1
            word = js[i:j]
            if c.isdigit():
                prev = "value"
            elif prev == "punct:.":  # a property name, even one that spells a keyword (arr.with(0, 1) is a read)
                prev = "name"
            elif word == "with":
                return None
            elif word in JS_REGEX_AFTER_WORD or word in JS_UNCLEAR_AFTER_WORD or word in JS_CONTROL_WORDS:
                prev = "word:" + word
            else:
                prev = "name"
            out.append(word)
            i = j
        elif js.startswith("...", i):
            out.append("...")
            prev, i = "punct:,", i + 3  # a spread: a / after it starts a regex
        elif js.startswith(("<!--", "-->"), i):
            return None
        else:
            if c == "(":
                opens.append("ctl" if prev.startswith("word:") and prev[5:] in JS_CONTROL_WORDS
                             else "await" if prev == "word:await" else "")
                prev = "punct:("
            elif c == ")":
                kind = opens.pop() if opens else ""
                prev = "ctl)" if kind == "ctl" else "await)" if kind == "await" else ")"
            elif c == "[" and prev in ("name", "value", ")", "punct:]", "punct:."):
                # 2026-11-17: a member access (x[...], f()[...], x?.[...]), not an array. A key that is one string is read
                # as the member it names (x['constructor'] is x.constructor; x['persist:root'] is a plain read, @[#]);
                # an escape in it asks; any other key is marked @[ for JS_ACTION
                last_word = next((p for p in reversed(out) if p != " "), "")
                m = JS_STRING_KEY.match(js, i) if last_word not in JS_GLOBAL_ALIASES else None
                if m:
                    if "\\" in m.group(2):
                        return None
                    named = re.fullmatch(r"[A-Za-z_$][\w$]*", m.group(2))
                    out.append("." + m.group(2) if named else "@[#]")
                    prev, i = ("name" if named else "punct:]"), m.end()
                    continue
                c, prev = "@[", "punct:["
            else:
                prev = "punct:" + c
            out.append(c)
            i += 1
    return "".join(out)


def _test_file_push(cmd, cwd):
    """2026-11-16 (rebuilt after the review): True only when the WHOLE raw command is plain test-file pushes into a QA
    folder on a tablet, judged on the text as typed (nothing the shell could reshape): optionally the exact cd into the
    workspace, then one or more parts joined by ' && ', each exactly 'adb -s <serial> push <source>... <dest>',
    'adb -s <serial> shell mkdir -p <QA folder>' or 'adb -s <serial> shell ls -la <QA folder>', with at least one push.
    Only letters, digits, space and . _ : / - in each part: no quotes, $, backtick, ; | & ( ) { } [ ] , * ? ~ \\ < > #,
    newline, env assignment, wrapper or global adb option. Each source is tests/<folder>/files/<name> under the
    workspace (the cd above, else the hook's cwd must be the workspace), a regular file, no link, and its real path is
    that same place. Each destination is /sdcard/Download/QA-<name>/ or /sdcard/Download/WingtraQA/, optionally with a
    file name; no '..'. Second review (same day): the text must be ASCII and is not stripped of Unicode spaces (zsh
    keeps them in a word), the serial must be a registered tablet (a profile <serial>.vN.json in qa/devices), and a
    source with more than one hard link does not pass."""
    if not cmd.isascii():
        return False
    text = cmd.strip(" \n")
    prefix = 'cd "' + WORKSPACE + '" && '
    if text.startswith(prefix):
        text = text[len(prefix):]
    elif not isinstance(cwd, str) or os.path.realpath(cwd) != os.path.realpath(WORKSPACE):
        return False
    try:
        serials = {m.group(1) for m in (re.fullmatch(r"([A-Za-z0-9]+)\.v\d+\.json", n)
                                        for n in os.listdir(os.path.join(QA_DIR, "devices"))) if m}
    except OSError:
        return False
    tests_dir = os.path.realpath(os.path.join(WORKSPACE, "tests"))
    pushed = False
    for part in text.split(" && "):
        if not re.fullmatch(r"[A-Za-z0-9 ._:/-]+", part):
            return False
        toks = part.split(" ")
        if len(toks) < 5 or toks[:2] != ["adb", "-s"] or toks[2] not in serials:
            return False
        verb = toks[3:]
        if verb[0] == "push":
            sources, dest = verb[1:-1], verb[-1]
            if not sources or not PUSH_DEST.fullmatch(dest):
                return False
            for s in sources:
                path = os.path.join(WORKSPACE, s)
                real = os.path.realpath(path)
                if (not PUSH_SOURCE.fullmatch(s) or os.path.islink(path) or not os.path.isfile(real)
                        or os.stat(real).st_nlink != 1
                        or os.path.dirname(os.path.dirname(os.path.dirname(real))) != tests_dir
                        or real != os.path.join(tests_dir, s[len("tests/"):])):
                    return False
            pushed = True
        elif len(verb) == 4 and verb[:3] in (["shell", "mkdir", "-p"], ["shell", "ls", "-la"]) \
                and PUSH_DEST.fullmatch(verb[3]):
            continue
        else:
            return False
    return pushed


def _safety_write(cmd):
    """G2: shell or inline-code writes to the engine's own safety files."""
    if SAFETY_SHELL_WRITE.search(cmd):
        return "ask", "a shell write " + SAFETY_REASON
    for code in _code_bodies(cmd):
        if SAFETY_LITERAL.search(code) and CODE_WRITE.search(code):
            return "ask", "inline code " + SAFETY_REASON
    return "pass", ""


MAC_ESCAPES = {'"': "\x01", "\\": "\x02", "$": "\x03", "`": "\x04", "'": "\x05", " ": "\x06"}


def _protect_escapes(cmd):
    """2026-11-17: cmd with the escapes of this Mac's shell that shlex (posix=False) misreads replaced by placeholder
    characters: \\" \\\\ \\$ \\` inside double quotes; those and \\' and an escaped space outside quotes. _unprotect
    gives back the plain character (the shell drops the backslash). Single-quoted text stays as typed."""
    out, i, n, dq = [], 0, len(cmd), False
    while i < n:
        c = cmd[i]
        if c == "'" and not dq:
            j = cmd.find("'", i + 1)
            if j < 0:
                out.append(cmd[i:])
                break
            out.append(cmd[i:j + 1])
            i = j + 1
            continue
        if c == "\\" and i + 1 < n and cmd[i + 1] in MAC_ESCAPES and (not dq or cmd[i + 1] in '"\\$`'):
            out.append(MAC_ESCAPES[cmd[i + 1]])
            i += 2
            continue
        if c == '"':
            dq = not dq
        out.append(c)
        i += 1
    return "".join(out)


def _unprotect(text):
    for ch, mark in MAC_ESCAPES.items():
        text = text.replace(mark, ch)
    return text


def _device_shells(cmd, rest=None, mac_dollar=None):
    """The device-side command line of each adb shell / exec-out call. A quoted piece runs on the tablet whole
    (its redirects too); an unquoted redirect belongs to this Mac's shell. 2026-11-16 (the person's OK, "Yes, fix
    them"): pieces that touch in the typed text ('r'm, "t"ee, 'a'db) are one word, as this Mac's shell joins them, and
    a call with no command comes back as an empty line (that shell runs whatever this Mac pipes into it). When rest is
    a list, the line's words outside every tablet line and this Mac's redirects go into it. 2026-11-17: this Mac's
    escapes (\\" inside double quotes, \\" or an escaped space outside them) are read as the shell reads them."""
    prot = _protect_escapes(cmd)
    try:
        lex = shlex.shlex(prot, posix=False, punctuation_chars=";&|()")
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:  # quotes shlex cannot pair: everything after each adb shell/exec-out counts as device-side
        if rest is not None:
            rest.append(cmd)
        bodies = [m.group(1) for m in re.finditer(r"(?:^|[\s/;&|(])adb\b[^\n;&|]*?\b(?:shell|exec-out)\b(.*)", cmd)]
        if mac_dollar is not None:
            mac_dollar.extend(True for _ in bodies)
        return bodies
    # [text with its outer quotes dropped, first raw token, a $ or backtick this Mac fills in]; separators stay apart
    words, pos, end = [], 0, None
    for t in toks:
        p = prot.find(t, pos)
        piece = _unprotect(t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "'\"" else t)
        mac = t[:1] != "'" and bool(re.search(r"[$`]", t))
        if words and p >= 0 and p == end and t not in ADB_SEP_TOKENS and words[-1][1] not in ADB_SEP_TOKENS:
            words[-1][0] += piece  # touches the word before it: one word
            words[-1][2] = words[-1][2] or mac
        else:
            words.append([piece, t, mac])
        if p >= 0:
            pos = end = p + len(t)
        else:
            end = None

    def bare(w):
        return re.sub(r"['\"\\]", "", w[0])

    out, i, used = [], 0, set()  # used: words that are a tablet line or one of this Mac's redirects
    while i < len(words):
        if os.path.basename(bare(words[i])) != "adb":
            i += 1
            continue
        j = i + 1
        while j < len(words) and words[j][1] not in ADB_SEP_TOKENS and bare(words[j]) not in ("shell", "exec-out"):
            j += 1
        if j >= len(words) or words[j][1] in ADB_SEP_TOKENS:
            i = j
            continue
        body, k, mac = [], j + 1, False
        while k < len(words) and words[k][1] not in ADB_SEP_TOKENS:
            t = words[k][1]
            if REDIRECT.match(t):
                n = 2 if re.fullmatch(r"\d*(>>?|<|&>)", t) else 1  # this Mac's redirect and its file
                used.update(range(k, k + n))
                k += n
                continue
            body.append(words[k][0])
            mac = mac or words[k][2]
            used.add(k)
            k += 1
        out.append(" ".join(body))
        if mac_dollar is not None:
            mac_dollar.append(mac)
        i = k
    if rest is not None:
        k = 0
        while k < len(words):
            if k not in used and REDIRECT.match(words[k][1]):
                k += 2 if re.fullmatch(r"\d*(>>?|<|&>)", words[k][1]) else 1
                continue
            if k not in used:
                rest.append(words[k][0])
            k += 1
    return out


def _xargs_adb_risky(cmd):
    """2026-11-17: True when xargs runs adb with words from its input where they matter: as adb's command (or with no
    command, appended), or in the tablet line when the input is not adb's own output (a listing of the tablet is data
    already there). A serial filled in for -s is fine. A pipeline the gate cannot split asks."""
    for line in re.split(r"&&|\|\||;|\n", cmd):
        stages = re.split(r"(?<!\|)\|(?!\|)", line)
        for k, stage in enumerate(stages):
            m = re.match(r"\s*xargs\b(.*)", stage, re.S)
            if not m or not re.search(r"\badb\b", stage):
                continue
            try:
                toks = shlex.split(m.group(1), posix=True)
            except ValueError:
                return True
            place, i = None, 0
            while i < len(toks) and toks[i].startswith("-"):
                o = toks[i]
                if o in ("-I", "-J"):
                    place, i = (toks[i + 1] if i + 1 < len(toks) else None), i + 2
                elif o.startswith(("-I", "-J")):
                    place, i = o[2:], i + 1
                elif o == "-i":
                    place, i = "{}", i + 1
                else:
                    i += 2 if o in ("-n", "-L", "-s", "-P", "-E", "-d", "-a") else 1
            run = toks[i:]
            if not run or os.path.basename(run[0]) != "adb" or not place:
                return True  # another program, or words appended where the gate cannot place them
            words = " ".join(run[1:]).replace(place, "\x07").split()
            for j in range(len(words) - 1, 0, -1):  # a placeholder that is only -s's serial is fine
                if words[j - 1] == "-s" and words[j] == "\x07":
                    del words[j - 1:j + 1]
            if not any("\x07" in w for w in words):
                continue
            at = _adb_command_at(words)
            if at is None or at >= len(words) or "\x07" in words[at]:
                return True  # the filled-in word is adb's command
            producer = "|".join(stages[:k]).strip()
            if not re.match(r"adb\b", producer):
                return True  # tablet line words from text the line made up (echo ';touch x' | xargs ...)
    return False


def _has_dollar_quote(cmd):
    """True when cmd opens $'...' (ANSI-C) or $"..." quoting outside other quotes: the shell turns it into any
    characters, escapes included. A $ before a closing quote ('\\.tif$') or inside quotes is not one."""
    i, n, dq = 0, len(cmd), False
    while i < n:
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if dq:
            dq = c != '"'
        elif c == "$" and i + 1 < n and cmd[i + 1] in "'\"":
            return True
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                return False
            i = j
        elif c == '"':
            dq = True
        i += 1
    return False


def _mac_values(cmd):
    """2026-11-17: the text this Mac's shell fills into words at run time on cmd, as far as the line shows it: the
    values of NAME=value words, for-loop word lists, and the commands inside $(...) and backticks outside single quotes.
    In a $(...) only ; & < > and write words count (a pipe there feeds its output, which is a program's, not the
    line's). A value read from a file or a program's output is not seen."""
    out, subs, i, n, dq = [], [], 0, len(cmd), False
    while i < n:
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if c == "'" and not dq:
            j = cmd.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            dq = not dq
        elif c == "`" or cmd.startswith("$(", i):
            try:
                j = _sub_end(cmd, i)
            except ValueError:
                subs.append(cmd[i:])
                break
            subs.append(cmd[i + (1 if c == "`" else 2):j - 1])
            i = j
            continue
        i += 1
    for m in re.finditer(r"(?:^|[\s;&|(])[A-Za-z_]\w*\+?=(\(?(?:'[^']*'|\"[^\"]*\"|[^\s;&|]*))", cmd):
        out.append(m.group(1))
    for m in re.finditer(r"\bfor\s+\w+\s+in\s+(.*?)\s*(?:;|\n)\s*do\b", cmd, re.S):
        out.append(m.group(1))
    for m in re.finditer(r"<<<\s*('[^']*'|\"[^\"]*\"|\S+)", cmd):  # read X <<< '...'
        out.append(m.group(1))
    return " ".join(out + [re.sub(r"\|", " ", s) for s in subs])


def _adb_rule(a, test_push, anchored=False):
    """The adb rules for one adb command string a (adb's arguments from its command on): ('deny', why), ('ask', why)
    or None. 2026-11-17: one place, used for every adb mention and again for every adb call the line really runs; for
    those (anchored) only the rules read at adb's command, since a quoted tablet pipeline is one word there."""
    if re.match(r"kill-server\b", a):
        return "deny", "never adb kill-server: it drops every tablet in a parallel run"
    if re.match(r"forward\s+--remove-all\b", a):
        return "deny", "never adb forward --remove-all: other targets' CDP forwards would drop"
    if re.match(r"forward\b", a) and re.search(r"\btcp:9333\b", a):
        return "deny", "CDP port 9333 is retired; use the profile port (9341 + slot)"
    if re.match(r"forward\s+tcp:", a) and "--no-rebind" not in a:
        return "deny", "use adb forward --no-rebind (check adb forward --list first)"
    # 2026-11-16: exec-out and exec-in run a tablet command as shell does (monkey and sendevent: see
    # _device_program_risk, which reads them only where a command starts)
    if (not anchored and re.search(r"(^|\s)(shell|exec-out|exec-in)\s+.*\binput\s+(tap|swipe|text|keyevent|press|roll|"
                                   r"motionevent|draganddrop|keycombination)\b", a)) \
            or re.match(r"(shell|exec-out|exec-in)\s+input\b", a):
        return "deny", "raw adb input is not allowed: every tap and keystroke goes through act.py"
    hit = None
    for rx, what in (() if anchored else ADB_ACTION_ASK):
        if rx.search(a):
            hit = ("ask", f"adb {what}: needs your OK")
    if re.match(r"push\b", a) and not test_push:
        hit = ("ask", "adb push copies files onto the tablet (a file of the same name is overwritten): needs your OK; "
                      "only test files from tests/<folder>/files/ into a QA folder under /sdcard/Download/ go without "
                      "asking")
    return hit


def _outside_single_quotes(line):
    """line with the text of each single-quoted part (one outside double quotes) emptied: the tablet's shell reads it as
    plain text, never as a redirect or a $ value (awk '$3 > 1000' is a comparison, not a write)."""
    out, i, n, dq = [], 0, len(line), False
    while i < n:
        c = line[i]
        if c == "\\":
            out.append(line[i:i + 2])
            i += 2
            continue
        if c == '"':
            dq = not dq
        elif c == "'" and not dq:
            j = line.find("'", i + 1)
            if j < 0:
                out.append(line[i:])
                break
            out.append("''")
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _device_program_risk(line):
    """2026-11-16 (the person's OK, "Yes, fix them"; after the review of the same day): (decision, reason) when a
    program the tablet line runs writes files, runs a shell or text as commands, injects input, or is computed at run
    time; None when every program only reads. A program is read where a command starts: after ; && || | & ( ) and
    newlines, shell keywords, NAME=value words, find's -exec, and wrappers with their options and operands (env, nice,
    timeout 5, toybox, xargs -I{}, run-as <app>). The line is split as the tablet's shell splits it (quotes and
    backslashes resolved); quotes it could not pair ask. 2026-11-17 (after the review): each $(...) or backtick is one
    word _ in its command and its own command line is judged too ($((...)) is arithmetic: only what it nests), and a
    case pattern before its ) is no command."""
    line, inner = _device_substitutions(line)
    for sub in inner:
        risk = _device_program_risk(sub)
        if risk:
            return risk
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()\n")
        lex.whitespace, lex.whitespace_split, lex.commenters = " \t\r", True, ""
        toks = list(lex)
    except ValueError:
        return "ask", "a tablet command whose quotes the gate cannot pair: needs your OK"
    cmds, cur, depth = [], [], 0
    for t in toks:
        if t and all(ch in ";&|()\n" for ch in t) or t in ("-exec", "-execdir", "-ok", "-okdir"):
            if ")" in t and depth <= 0 and cur:
                cur = []  # a case pattern (SM-*) ...): no command
            depth += t.count("(") - t.count(")")
            cmds.append(cur)
            cur = []
        else:
            cur.append(t)
    cmds.append(cur)
    for words in cmds:
        i, wrappers = 0, []
        while i < len(words):
            w = words[i]
            if w in DEVICE_KEYWORDS or re.match(r"[A-Za-z_]\w*=", w):
                i += 1
                continue
            name = w.rsplit("/", 1)[-1]
            if name not in DEVICE_WRAPPERS:
                break
            opts, operands = DEVICE_WRAPPERS[name]
            wrappers.append(name)
            i += 1
            while i < len(words) and words[i].startswith("-") and words[i] != "-":
                i += 2 if words[i] in opts else 1
            i += operands
        if i >= len(words):
            if "run-as" in wrappers:
                return "ask", "adb shell run-as with no command opens a shell as the app on the tablet: needs your OK"
            continue
        w, args = words[i], words[i + 1:]
        prog = w.rsplit("/", 1)[-1]
        if w not in ("[", "[[") and re.search(r"[$`{}*?\[]", w):
            return "ask", ("a tablet command whose program is filled in at run time ($, a backtick, braces or a glob): "
                           "needs your OK")
        if prog == "sendevent" or prog == "input" and args[:1] and args[0] in RAW_INPUT_VERBS \
                or prog == "cmd" and args[:1] == ["input"]:
            return "deny", "raw adb input is not allowed: every tap and keystroke goes through act.py"
        if prog == "monkey":
            return "ask", "adb shell monkey sends random taps to the tablet (am start launches the app): needs your OK"
        # 2026-11-17: the ADB_ACTION_ASK changes read where they run on the tablet (adb shell "ls; pm clear <app>")
        sub = args[0] if args else ""
        if (prog == "pm" and sub in DEVICE_PM_CHANGES
                or prog == "cmd" and (sub == "package" and args[1:2] and args[1] in DEVICE_PM_CHANGES
                                      or sub == "wifi" or sub == "settings" and args[1:2] and args[1] in ("put", "delete", "reset"))
                or prog == "settings" and sub in ("put", "delete", "reset")
                or prog == "svc" and sub in ("wifi", "power", "data", "usb")
                or prog == "appops" and sub in ("set", "reset")
                or prog in ("reboot", "install", "uninstall")):
            return "ask", f"adb shell {prog} {sub} changes the tablet (an app, a setting, Wi-Fi or power): needs your OK"
        if prog in DEVICE_SHELLS:
            return "ask", "adb shell runs a shell, or text as commands, on the tablet, which the gate cannot read: needs your OK"
        if prog in ("awk", "nawk", "gawk") and _awk_risky(args):
            return "ask", "awk on the tablet with system(), getline, a print to a file or a pipe, or a program file: needs your OK"
        if prog == "sed" and any(re.search(r"(?:^|[;}/\s])[wWe](?:\s|$)", a) for a in args):
            return "ask", "sed on the tablet that writes a file or runs a command (w, W, e): needs your OK"
        if (prog in DEVICE_FILE_WRITERS or prog == "tar" and _tar_writes(args)
                or prog == "unzip" and not any(re.fullmatch(r"-[a-zA-Z]*[lvtpZ][a-zA-Z]*", a) for a in args)
                or prog in DEVICE_PACKERS and _device_operands(args, set()) and not any(
                    re.fullmatch(r"-[a-zA-Z]*[clt][a-zA-Z]*|--(?:stdout|to-stdout|list|test)", a) for a in args)
                or prog == "screencap" and _screencap_file(args)
                or prog == "screenrecord" and _screenrecord_file(args)
                or prog == "uniq" and len(_device_operands(args, {"-w", "-f", "-s"})) >= 2):
            return "ask", f"adb shell {prog} creates, unpacks or overwrites files on the tablet: needs your OK"
    return None


RAW_INPUT_VERBS = {"tap", "swipe", "text", "keyevent", "press", "roll", "motionevent", "draganddrop", "keycombination"}
DEVICE_PM_CHANGES = {"clear", "install", "install-create", "install-write", "install-commit", "uninstall", "grant",
                     "revoke", "disable", "disable-user", "enable", "suspend", "unsuspend", "hide", "unhide",
                     "reset-permissions", "trim-caches", "set-installer", "set-home-activity", "set-app-links",
                     "default-state"}


def _device_substitutions(line):
    """2026-11-17: line with each $(...), $((...)) and backtick part replaced by the word _ (outside single quotes),
    and the command lines they run: the inside of $(...) and backticks, and of a $(...) nested in $((...)) arithmetic.
    An unclosed part leaves the line as it is."""
    out, inner, i, n = [], [], 0, len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            out.append(line[i:i + 2])
            i += 2
            continue
        if c == "'":
            j = line.find("'", i + 1)
            if j < 0:
                out.append(line[i:])
                break
            out.append(line[i:j + 1])
            i = j + 1
            continue
        if c == "`" or line.startswith("$(", i):
            try:
                j = _sub_end(line, i)
            except ValueError:
                return line, inner
            body = line[i + 1:j - 1] if c == "`" else line[i + 2:j - 1]
            if c == "$" and body.startswith("(") and body.endswith(")"):
                inner += _device_substitutions(body[1:-1])[1]  # $((...)): arithmetic, only what it nests runs
            else:
                inner.append(body)
            out.append("_")
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out), inner


def _device_operands(args, value_options):
    """The operands of a tablet program's arguments: words that are not an option or a value_options option's value."""
    out, j = [], 0
    while j < len(args):
        a = args[j]
        if a == "--":
            return out + args[j + 1:]
        if a in value_options:
            j += 2
            continue
        if not a.startswith("-") or a == "-":
            out.append(a)
        j += 1
    return out


def _awk_risky(args):
    """True when an awk call may write or run commands: system(), getline, print or printf to a file or a pipe, a program
    file (-f), or no program the gate can see."""
    j = 0
    while j < len(args):
        a = args[j]
        if a == "--":
            j += 1
            break
        if a in ("-F", "-v"):
            j += 2
        elif a.startswith("-f") or a == "--file":
            return True
        elif a.startswith("-"):
            j += 1
        else:
            break
    prog = args[j] if j < len(args) else None
    if prog is None:
        return True
    prog = re.sub(r'"(?:\\.|[^"\\])*"', '""', prog)  # awk's own "..." strings are text (print $5 " | " $NF)
    return bool(re.search(r"\bsystem\s*\(|\bgetline\b|\bprintf?\b[^;}]*[|>]", prog))


def _tar_writes(args):
    """True when tar on the tablet extracts, appends or updates, or creates an archive file that is not - (stdout)."""
    letters, fval = "", None
    for j, a in enumerate(args):
        if a.startswith("--"):
            name, _, val = a.partition("=")
            if name in ("--extract", "--get", "--delete", "--append", "--update", "--to-command", "--overwrite"):
                return True
            if name == "--file":
                fval = val or (args[j + 1] if j + 1 < len(args) else None)
            elif name == "--create":
                letters += "c"
        elif a.startswith("-") or j == 0:
            cluster = a.lstrip("-")
            letters += cluster
            if "f" in cluster:
                fval = cluster[cluster.index("f") + 1:] or (args[j + 1] if j + 1 < len(args) else None)
    return any(c in letters for c in "xru") or ("c" in letters and fval not in (None, "-"))


def _screencap_file(args):
    """True when screencap writes its picture to a file on the tablet (a FILENAME after its options; -d takes an id)."""
    j = 0
    while j < len(args):
        if args[j] == "-d":
            j += 2
        elif args[j].startswith("-"):
            j += 1
        else:
            return True
    return False


def _screenrecord_file(args):
    """True when screenrecord writes a video file on the tablet (its output is not -, stdout)."""
    out, j = None, 0
    while j < len(args):
        a = args[j]
        if a in SCREENRECORD_VALUE_OPTIONS:
            j += 2
            continue
        if not a.startswith("-") or a == "-":
            out = a
        j += 1
    return out != "-"


def _key_glob(plain):
    """True when a glob word can expand to the approval key: its last part matches approval.key and the last part of
    its folder matches state ({a,b} is read as *)."""
    for w in re.split(r"[\s;&|()<>=]+", plain):
        if not re.search(r"[*?\[{]", w):
            continue
        folder, _, name = re.sub(r"\{[^}]*\}?", "*", w).rpartition("/")
        if folder and fnmatch.fnmatchcase(APPROVAL_KEY, name) and \
                fnmatch.fnmatchcase("state", folder.rpartition("/")[2]):
            return True
    return False


# ---- 2026-11-04: the two older gaps the 2026-11-03 review found (the person's OK, 2026-11-04) ----
# (a) The CT password file. The old rule let a line through whenever it also mentioned a qa/runner script anywhere, even
# in a comment or after a ; (cat qa/secrets.env; python3 -I qa/runner/status.py). The runner reads the file itself and
# never needs its name on a line. A shell line is now denied when it
#  - names the file: as typed; without quotes, backslashes and line continuations; with $'...' read; ignoring case (this
#    Mac's disk ignores it and folds ſ to s); or once the line's own variables are filled in (F=secrets; ${X:-secrets},
#    ${X/z/s}, X+=..., $~F; ~, ~+, ~-);
#  - has a glob the shell expands that can reach it: in the qa folder (qa/s*, QA/*.env, qa/**/*.env, qa/{secrets,x}.env,
#    {qa/s,}ecrets.env, qa/secret[[:alpha:]].env, zsh's qa/secret(s).env and (qa|x)/secret?.env, <qa/secret?.env), or
#    bare where the command runs in qa (cd qa && cat *; the folder followed through cd, chdir, builtin cd, pushd, popd,
#    cd -, env -C);
#  - reads, copies, archives, links or serves the qa folder whole (grep -r, rg, cp -R, rsync -a/-d, tar, zip -r, ln,
#    diff -r, git diff/grep --no-index, find -exec, hdiutil -srcfolder, python -m http.server/zipfile/tarfile, aws s3,
#    rclone), or links a folder above it, unless a filter leaves the file out (--include='*.py', --exclude='*.env',
#    rg -g '!*.env', rg -t py, find -name '*.py') or it prints only names or counts (grep -l/-c/-q, rg -l, diff -q);
#  - runs such a line through a shell (bash -c '...', eval, trap, env -S, a here-string, find -exec).
# The Read, Write and Edit tools and Grep get the same checks (decide). Beyond any line check: code that builds a name
# at run time (python -c "open('qa/secr'+'ets.env')"), a script written first and run later, a pipe or xargs that hands
# names to a reader, shell features that run code from data (arithmetic subscripts, zsh glob qualifiers), arrays, cd
# inside subshells, functions or || chains, CDPATH, links made on the same line, and a recursive read of a folder above
# qa (grep -r x . at the workspace root; the Bash tool's grep honours qa/.gitignore and ripgrep qa/.ignore there).
SECRETS_NAME = "secrets.env"
SECRETS_WORD = re.compile(r"(?<![\w.-])secrets\.env\b", re.I)
SECRETS_REASON = "qa/secrets.env holds the CT test-account password; only qa/runner reads it, nothing prints or copies it"
QA_HOME = os.path.join(WORKSPACE, "qa")
SECRETS_HOME = os.path.join(QA_HOME, SECRETS_NAME)
QA_DIR_REASON = ("reads, copies or serves the whole qa folder, which holds qa/secrets.env (the CT test-account "
                 "password): name its subfolders instead (qa/app, qa/cases, qa/runner ...), or leave the file out with "
                 "a filter (--include='*.py', --exclude='*.env')")
UNKNOWN_DIR = "?"  # a folder only the run knows (for a line run from inside another)
FIRMLINK = "/System/Volumes/Data"
SHELL_CODE = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "yash", "fish", "csh", "tcsh"}
OPEN = GLOB_MARKS["{"]
_MARK = str.maketrans(GLOB_MARKS)
_UNMARK = str.maketrans({v: k for k, v in GLOB_MARKS.items()})
GLOB_MARKED = re.compile("[" + GLOB_MARKS["*"] + GLOB_MARKS["?"] + GLOB_MARKS["["] + "]")
PS_SPLIT = re.compile(SPLIT.pattern + r"|[<>]\(")
# A zsh glob group: (a|b) glued to a word, or with | inside and a word after it (not $( <( >( =( or a subshell)
ZSH_GROUP = re.compile(r"(?<=[^\s$<>=;&|(])\(([^()\s]*)\)|\(([^()\s]*\|[^()\s]*)\)(?=[^\s;&|)<>])")
POSIX_CLASSES = {"alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "upper": "A-Z", "lower": "a-z",
                 "space": r"\s", "blank": r" \t", "punct": r"!-/:-@\[-`{-~", "xdigit": "0-9A-Fa-f", "word": r"\w",
                 "cntrl": r"\x00-\x1f", "print": r" -~", "graph": r"!-~"}
GREP_PROGS = {"grep", "egrep", "fgrep", "ggrep", "ugrep", "rgrep"}
GREP_VALUES = "efdDmABCX"
GREP_VALUE_LONGS = ("--regexp", "--file", "--max-count", "--after-context", "--before-context", "--context",
                    "--directories", "--devices", "--label", "--exclude-dir")
RG_VALUES = "ABCEMTdefgjmrt"
RG_VALUE_LONGS = ("--glob", "--iglob", "--type", "--type-not", "--max-count", "--context", "--after-context",
                  "--before-context", "--regexp", "--file", "--replace", "--encoding", "--max-depth", "--threads",
                  "--max-columns", "--type-add", "--sort", "--sortr", "--pre", "--pre-glob", "--colors", "--color",
                  "--path-separator", "--max-filesize", "--engine", "--field-match-separator", "--ignore-file")
NAMES_ONLY_LONG = ("--files-with-matches", "--files-without-match", "--count", "--count-matches", "--quiet",
                   "--silent", "--files", "--brief", "--name-only")
DEST_LAST = {"cp", "gcp", "rsync", "scp", "ditto", "ln", "gln"}
# (b) ClickUp's REST API: when a shell line names api.clickup.com (in any spelling: URL-encoded, split by a line
# continuation, in a {a,b} or [a-b] glob, built from the line's variables), it asks if a command on it can send: curl or
# wget with a method other than GET or HEAD, data, a form, an upload or a config; another network client; code (python,
# node, a shell's -c, eval, ssh's remote line, env -S, find -exec) that names the host or reads code from stdin; awk with
# system or getline; sort --compress-program; a variable that loads code into an interpreter (PERL5OPT, NODE_OPTIONS,
# CURL_HOME ...). A connector or browser call naming it asks too (the ClickUp connector's tools keep their own rules).
# Both come last, so they never pre-empt a deny; comments over REST stay denied (CLICKUP_REST_COMMENT).
CLICKUP_API = re.compile(r"api\.clickup\.com", re.I)
CLICKUP_HINT = re.compile(r"c.{0,12}l.{0,12}i.{0,12}c.{0,12}k.{0,12}u.{0,12}p", re.I | re.S)
CLICKUP_API_REASON = "calls ClickUp's API directly: a change in ClickUp needs the person's OK"
NET_CLIENTS = {"http", "https", "xh", "xhs", "httpie", "aria2c", "lwp-request", "GET", "POST", "PUT", "HEAD", "nc",
               "ncat", "netcat", "socat", "telnet", "openssl", "fetch", "websocat", "grpcurl"}
CODE_RUNNERS = {"node", "nodejs", "deno", "bun", "ruby", "perl", "php", "osascript", "swift", "java", "jshell", "lua",
                "luajit", "Rscript", "R", "julia", "tclsh", "wish", "expect", "pwsh", "powershell", "groovy", "kotlin",
                "scala", "go", "dotnet", "make", "gmake", "npm", "npx", "pnpm", "yarn", "uv", "uvx", "pipx", "parallel",
                "watch", "script", "screen", "tmux", "at", "batch", "launchctl", "source", "."}
CODE_ARG_FLAGS = {"-c", "-m", "-e", "-E", "-p", "-r", "--eval", "--print", "--command"}
INJECT_VARS = {"PERL5OPT", "PERL5LIB", "PERLLIB", "PYTHONSTARTUP", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS",
               "NODE_PATH", "RUBYOPT", "RUBYLIB", "BASH_ENV", "ENV", "ZDOTDIR", "CURL_HOME", "WGETRC", "LD_PRELOAD",
               "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "GIT_SSH_COMMAND", "http_proxy", "https_proxy",
               "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"}
CURL_VALUES = "AbcCdDeEFHiKmoOQrTuUwxXYyz"
HTTP_CONFIGS = {".curlrc", "_curlrc", ".wgetrc", ".netrc"}
ZSH_QUAL_CODE = re.compile(r"[*?\]]\([^()\s]*[e+]")
# MCP tools that can reach the network (Claude's own UI tools cannot)
# $((...)) arithmetic (read as a number); a line with [[ ]], (( )) or let, where $(...) inside quotes can still run;
# a part of a word only the run knows, in _expand(wild=True)
ARITH = re.compile(r"\$\(\((?:[^()`$]|\$(?!\()|\((?:[^()`$]|\$(?!\())*\))*\)\)")
ARITH_CTX = re.compile(r"\[\[|\(\(|(?:^|[\s;&|])let\s")
CMD_SUBST = re.compile(r"\$\(((?:[^()]|\([^()]*\))*)\)")
WILD = "\ue005"
NETWORK_MCP = re.compile(r"^mcp__(?!(?:ccd_\w+|visualize|scheduled-tasks|mcp-registry|plugin-authoring|"
                         r"Claude_Code_iOS_Simulator)__)")


def _fold(s):
    """s as this Mac's disk compares names: NFC, case folded (ſ is s)."""
    return unicodedata.normalize("NFC", s).casefold()


def _glob_rx(pattern):
    """A shell glob (one name) as a regex: * ? and [...] with ! or ^ negation, POSIX classes, [=c=] and [.c.]."""
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            out.append(".*")
        elif c == "?":
            out.append(".")
        elif c == "[":
            j = i + 1
            neg = j < n and pattern[j] in "!^"
            j += neg
            items, first = [], True
            while j < n and (pattern[j] != "]" or first):
                first = False
                m = re.match(r"\[([:=.])(\w+|.)\1\]", pattern[j:])
                if m:
                    items.append(POSIX_CLASSES.get(m.group(2), "") if m.group(1) == ":" else re.escape(m.group(2)))
                    j += m.end()
                elif pattern[j] == "\\" and j + 1 < n:
                    items.append(re.escape(pattern[j + 1]))
                    j += 2
                elif j + 2 < n and pattern[j + 1] == "-" and pattern[j + 2] != "]":
                    items.append(re.escape(pattern[j]) + "-" + re.escape(pattern[j + 2]))
                    j += 3
                else:
                    items.append(re.escape(pattern[j]))
                    j += 1
            if j >= n:
                out.append(re.escape("["))
            else:
                out.append("[" + ("^" if neg else "") + "".join(items) + "]")
                i = j
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 1
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _glob_match(name, pattern, fold=True):
    """True when a name matches a shell glob (case folded when fold, as this disk compares names)."""
    if fold:
        name, pattern = _fold(name), _fold(pattern)
    try:
        return re.fullmatch(_glob_rx(pattern), name, re.S) is not None
    except re.error:
        return False


def _norm_path(p):
    """p with . and .. read and the firmlink prefix dropped (/System/Volumes/Data/Users is /Users)."""
    p = os.path.normpath(p)
    return p[len(FIRMLINK):] if p.startswith(FIRMLINK + "/") else p


def _parts_match(pp, qq, fold=True):
    """True when path parts qq match glob parts pp; a ** part spans any number of folders (zsh's **/)."""
    if not pp:
        return not qq
    if pp[0] == "**":
        return _parts_match(pp[1:], qq, fold) or (bool(qq) and _parts_match(pp, qq[1:], fold))
    return bool(qq) and _glob_match(qq[0], pp[0], fold) and _parts_match(pp[1:], qq[1:], fold)


def _path_match(pattern, path, fold=True):
    """True when a path matches a glob pattern part by part (a * never spans a /), case ignored when fold."""
    return _parts_match(_norm_path(pattern).split("/"), _norm_path(path).split("/"), fold)


def _in_qa_home(path):
    """True when a folder is the workspace's qa folder itself (case ignored, firmlinks and links followed)."""
    try:
        if _path_match(path, QA_HOME):
            return True
        return _fold(_norm_path(os.path.realpath(path))) == _fold(_norm_path(os.path.realpath(QA_HOME)))
    except (OSError, ValueError, RecursionError):
        return False


def _is_qa(folder):
    """True when a folder's last part (glob characters read as patterns, case ignored) can be qa."""
    return _glob_match("qa", os.path.basename(_norm_path(folder)))


def _full(arg, base):
    """An operand as an absolute path from the folder its command runs in; None when that folder is unknown."""
    a = arg.rstrip("/") or "/"
    if os.path.isabs(a):
        return a
    return None if base is None else os.path.join(base, a)


def _is_qa_home(arg, base):
    """True when an operand (a glob or a path) can be the workspace's qa folder itself, read from the folder its command
    runs in (base); when only the run knows that folder, a relative operand counts when it is . or its last part can be
    qa."""
    full = _full(arg, base)
    if full is None:
        a = arg.rstrip("/")
        return a in (".", "") or _is_qa(a)
    if re.search(r"[*?\[]", full):
        return _path_match(full, QA_HOME)
    return _in_qa_home(full)


def _reaches_qa(arg, base):
    """True when an operand can be the qa folder or a folder above it (a link to one shows qa under a new name)."""
    full = _full(arg, base)
    if full is None:
        a = arg.rstrip("/")
        return a in (".", "..", "") or _is_qa(a)
    norm, qa = _norm_path(full), _norm_path(QA_HOME).split("/")
    if norm == "/":
        return True
    if not re.search(r"[*?\[]", norm):
        try:
            real = _norm_path(os.path.realpath(full))
        except (OSError, ValueError):
            real = norm
        return any(_fold(p) == _fold("/".join(qa[:k])) for p in (norm, real) for k in range(2, len(qa) + 1))
    return any(_parts_match(norm.split("/"), qa[:k]) for k in range(2, len(qa) + 1))


def _ansi_c_body(body):
    """The text a $'...' body stands for, read as bash and zsh read it (\\n \\t \\xH \\uHHHH \\UHHHHHHHH \\ooo \\cX)."""
    simple = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
              "\\": "\\", "'": "'", '"': '"', "?": "?"}
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        d = body[i + 1]
        if d in simple:
            out.append(simple[d])
            i += 2
        elif d in "xuU":
            m = re.match(r"[0-9a-fA-F]{1,%d}" % {"x": 2, "u": 4, "U": 8}[d], body[i + 2:])
            if m:
                try:
                    out.append(chr(int(m.group(0), 16)))
                except (ValueError, OverflowError):
                    pass
                i += 2 + m.end()
            else:
                out.append("\\" + d)
                i += 2
        elif d in "01234567":
            m = re.match(r"[0-7]{1,3}", body[i + 1:])
            out.append(chr(int(m.group(0), 8) & 0xFF))
            i += 1 + m.end()
        elif d == "c" and i + 2 < n:
            out.append(chr(ord(body[i + 2]) & 0x1F))
            i += 3
        else:
            out.append("\\" + d)
            i += 2
    return "".join(out)


ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.S)


def _ansi_c(text):
    """text with each $'...' word replaced by the text it stands for."""
    return ANSI_C.sub(lambda m: _ansi_c_body(m.group(1)), text)


def _ansi_c_quoted(text):
    """text with each $'...' word replaced by a single-quoted word of the same value (one literal word to a scan)."""
    return ANSI_C.sub(lambda m: "'" + _ansi_c_body(m.group(1)).replace("'", "'\\''") + "'", text)


def _spellings(cmd):
    """The line as typed; without quotes, backslashes and line continuations; with $'...' read; and each folded."""
    joined = cmd.replace("\\\n", "")
    base = (cmd, re.sub(r"['\"\\]", "", joined), re.sub(r"['\"\\]", "", _ansi_c(joined)))
    return base + tuple(_fold(s) for s in base)


def _zsh_group_texts(cmd, cap=16):
    """The line once per alternative of each zsh glob group (qa/secret(s).env, (qa|x)/secret?.env), at most cap."""
    if not ZSH_GROUP.search(cmd):
        return [cmd]
    out, todo = [], [cmd]
    while todo and len(out) < cap:
        t = todo.pop()
        m = ZSH_GROUP.search(t)
        if not m:
            out.append(t)
            continue
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        todo.extend(t[:m.start()] + alt + t[m.end():] for alt in inner.split("|"))
    return out or [cmd]


def _brace_seq(inner):
    """The words of a sequence brace ({1..3}, {a..e}, {1..9..2}); None when inner is not one."""
    for rx, conv, back in ((r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?", int, str), (r"(.)\.\.(.)(?:\.\.(-?\d+))?", ord, chr)):
        m = re.fullmatch(rx, inner)
        if m:
            a, b, step = conv(m.group(1)), conv(m.group(2)), abs(int(m.group(3) or 1)) or 1
            rng = range(a, b + 1, step) if a <= b else range(a, b - 1, -step)
            return [back(x) for x in list(rng)[:64]]
    return None


def _brace_span(w):
    """(start, end, alternatives) of the first brace expansion in w, opened by a marked {; None when there is none."""
    i = w.find(OPEN)
    while i >= 0:
        depth, j, commas = 0, i, []
        while j < len(w):
            if w[j] == OPEN:
                depth += 1
            elif w[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            elif w[j] == "," and depth == 1:
                commas.append(j)
            j += 1
        if j < len(w):
            if commas:
                cuts = [i] + commas + [j]
                return i, j + 1, [w[cuts[k] + 1:cuts[k + 1]] for k in range(len(cuts) - 1)]
            seq = _brace_seq(w[i + 1:j])
            if seq is not None:
                return i, j + 1, seq
        i = w.find(OPEN, i + 1)
    return None


def _brace_alts(word, cap=64):
    """The words a brace expansion makes of word (only a { kept as a glob mark, outside quotes, opens one)."""
    out, todo = [], [word]
    while todo:
        if len(out) + len(todo) > cap:
            return out + todo
        w = todo.pop()
        span = _brace_span(w)
        if span is None:
            out.append(w)
        else:
            s, e, alts = span
            todo.extend(w[:s] + a + w[e:] for a in alts)
    return out


def _brace_plain(text, cap=64):
    """The words a brace expansion makes of a plain text (every { counts)."""
    return [a.translate(_UNMARK) for a in _brace_alts(text.replace("{", OPEN), cap)]


def _tilde(word, env):
    """word with a leading ~, ~user, ~+, ~- or zsh's ~name (a variable holding a folder) read; None when unknown."""
    if not word.startswith("~"):
        return word
    m = re.match(r"~([^/]*)", word)
    head, rest = m.group(1), word[m.end():]
    if head == "":
        base = env.get("HOME")
    elif head == "+":
        base = env.get("PWD")
    elif head == "-":
        base = env.get("OLDPWD")
    elif re.fullmatch(r"[A-Za-z_]\w*", head) and (env.get(head) or "").startswith("/"):
        base = env[head]
    else:
        e = os.path.expanduser("~" + head)
        base = None if e.startswith("~") else e
    return None if base is None else base + rest


def _close_brace(w, j):
    """Index of the } that closes the brace at w[j]; None when it does not close."""
    depth = 0
    for k in range(j, len(w)):
        if w[k] in ("{", OPEN):
            depth += 1
        elif w[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    return None


def _strip_pat(v, pat, prefix, longest):
    """${v#pat} ${v##pat} ${v%pat} ${v%%pat}."""
    rx = re.compile(_glob_rx(pat), re.S)
    if prefix:
        for i in (range(len(v), -1, -1) if longest else range(len(v) + 1)):
            if rx.fullmatch(v[:i]):
                return v[i:]
    else:
        for i in (range(len(v) + 1) if longest else range(len(v), -1, -1)):
            if rx.fullmatch(v[i:]):
                return v[:i]
    return v


def _replace_pat(v, pat, rep, every):
    """${v/pat/rep} ${v//pat/rep} ${v/#pat/rep} ${v/%pat/rep}."""
    anchor = pat[:1] if pat[:1] in "#%" else ""
    rx = re.compile(_glob_rx(pat[len(anchor):]), re.S)
    if anchor == "#":
        for i in range(len(v), -1, -1):
            if rx.fullmatch(v[:i]):
                return rep + v[i:]
        return v
    if anchor == "%":
        for i in range(len(v) + 1):
            if rx.fullmatch(v[i:]):
                return v[:i] + rep
        return v
    out, i, replaced = [], 0, False
    while i < len(v):
        hit = None
        if every or not replaced:
            for j in range(len(v), i, -1):
                if rx.fullmatch(v[i:j]):
                    hit = j
                    break
        if hit is None:
            out.append(v[i])
            i += 1
        else:
            out.append(rep)
            i, replaced = hit, True
    return "".join(out)


def _param(expr, env, mark):
    """The value of ${expr}: NAME, and NAME with :- - := = :+ + # ## % %% / // ^^ ,, ^ , :off:len; ~NAME (zsh); None
    when only the run would know it."""
    e = expr.translate(_UNMARK)
    tilde = e.startswith("~")
    m = re.match(r"([A-Za-z_]\w*)(.*)$", e[tilde:], re.S)
    if not m:
        return None
    name, op = m.group(1), m.group(2)
    v = env.get(name)

    def done(s):
        return s.translate(_MARK) if (mark or tilde) else s
    for o in (":-", ":=", ":+", ":?", "-", "=", "+", "?"):
        if op.startswith(o):
            arg = _expand(op[len(o):], env, mark)
            if arg is None or o.endswith("?"):
                return None
            unset = v is None or (o.startswith(":") and v == "")
            if o.endswith("+"):
                return "" if unset else arg
            return arg if unset else done(v)
    if v is None or len(v) > 400:
        return None
    if op == "":
        return done(v)
    if op[:1] in "#%":
        longest = op[:2] in ("##", "%%")
        pat = _expand(op[2 if longest else 1:], env)
        return None if pat is None else done(_strip_pat(v, pat, op[0] == "#", longest))
    if op[:1] == "/":
        every = op.startswith("//")
        pat, _, rep = op[2 if every else 1:].partition("/")
        pat, rep = _expand(pat, env), _expand(rep, env)
        return None if pat is None or rep is None else done(_replace_pat(v, pat, rep, every))
    if op in ("^^", ",,", "^", ","):
        return done({"^^": v.upper(), ",,": v.lower(), "^": v[:1].upper() + v[1:], ",": v[:1].lower() + v[1:]}[op])
    m = re.fullmatch(r":\s*(-?\d+)(?::\s*(-?\d+))?", op)
    if m:
        s = v[int(m.group(1)):] if int(m.group(1)) >= 0 else v[len(v) + int(m.group(1)):]
        if m.group(2) is not None:
            ln = int(m.group(2))
            s = s[:ln] if ln >= 0 else s[:len(s) + ln]
        return done(s)
    return None


def _expand(word, env, mark=False, wild=False):
    """word with its tilde and parameter expansions read from env (glob characters of a value kept as marks when mark,
    as an unquoted expansion is globbed); None when a part only the run would know is left, or with WILD in that part
    when wild."""
    w = _tilde(word, env)
    if w is None:
        if not wild:
            return None
        w = WILD + re.sub(r"^~[^/]*", "", word)
    out, i, n = [], 0, len(w)
    while i < n:
        c = w[i]
        if c in ("`", SUBST_MARK):
            if not wild:
                return None
            out.append(WILD)
            i += 1
            continue
        if c != "$":
            out.append(c)
            i += 1
            continue
        if i + 1 < n and w[i + 1] in ("{", OPEN):
            k = _close_brace(w, i + 1)
            val = None if k is None else _param(w[i + 2:k], env, mark)
            if val is None:
                if not wild or k is None:
                    return None
                val = WILD
            out.append(val)
            i = k + 1
            continue
        j = i + 1 + (i + 1 < n and w[i + 1] == "~")
        m = re.match(r"[A-Za-z_]\w*", w[j:])
        v = env.get(m.group(0)) if m else None
        if v is None:
            if not wild:
                return None
            out.append(WILD)
            i = j + (m.end() if m else int(j < n))
            continue
        out.append(v.translate(_MARK) if (mark or j > i + 1) else v)
        i = j + m.end()
    return "".join(out)


def _resolve(marked, env):
    """[(plain word, globbed)] for each word a marked word makes once expanded; None when only the run knows it."""
    w = _expand(marked, env, mark=True)
    if w is None:
        return None
    return [(a.translate(_UNMARK), bool(GLOB_MARKED.search(a))) for a in _brace_alts(w)]


def _prog_index(toks):
    """Index of the program word in a simple command (as _command finds it), or None."""
    i = 0
    while i < len(toks):
        t = toks[i].lstrip("(")
        name = os.path.basename(t)
        if not t or t in SHELL_WORDS:
            i += 1
        elif t == "function":
            i += 2
        elif re.match(r"^[A-Za-z_]\w*\+?=", t):
            i += 1
        elif name in WRAPPERS:
            i = _after_wrapper(toks, i + 1, *WRAPPERS[name])
        else:
            return i
    return None


def _contexts(cmd, cwd):
    """(commands, parsed): each simple command the line runs, in order, as a dict:
    prog; args (per argument the plain words its expansions make, None when only the run knows it); names and globs
    (plain words for the name and glob checks: arguments, redirect targets, assigned values, the program word; an
    argument with an unknown part in its last name also gives a glob, qa/$a.env reading as qa/*.env); base (the folder
    it runs in: the hook's working folder or the workspace root, followed through cd, chdir, builtin cd, pushd, popd,
    cd - and env -C; None when only the run knows it); wrappers; assigned (variables set on it); env_s (the line env -S
    runs); herestring; heredoc; piped; targets (redirect targets). Redirections and here-strings may stand anywhere in a
    command, also before its program. Variables set on the line (NAME=value, NAME+=value, export ...) are followed;
    $'...' is read first and $((...)) reads as a number. parsed is False when the gate's scan cannot read the line and
    the plain split stands in (every glob character counted)."""
    text = ARITH.sub("0", _ansi_c_quoted(cmd))
    try:
        scanned, parsed = [w for w in _sh_scan(text, glob_mark=True) if w], True
    except (ValueError, IndexError, RecursionError):
        plain = re.sub(r"['\"\\]", "", text.replace("\\\n", ""))
        scanned = [w for w in ([x.translate(_MARK) for x in seg.split()] for seg in PS_SPLIT.split(plain)) if w]
        parsed = False
    env = {k: v for k, v in (("HOME", os.path.expanduser("~")), ("TMPDIR", os.environ.get("TMPDIR")),
                             ("USER", os.environ.get("USER"))) if v}
    cur = None if cwd == UNKNOWN_DIR else _norm_path(cwd or WORKSPACE)
    old, stack, out = None, [], []
    for marked_all in scanned:
        words_all = [w.translate(_UNMARK) for w in marked_all]
        for key, val in (("PWD", cur), ("OLDPWD", old)):
            if val:
                env[key] = val
            else:
                env.pop(key, None)
        words, marked, targets, here, i = [], [], [], None, 0
        while i < len(words_all):
            w, m = words_all[i], marked_all[i]
            i += 1
            if w == HEREDOC_MARK:
                continue
            if w.startswith("<<<"):
                if len(w) > 3:
                    here = w[3:]
                elif i < len(words_all):
                    here, i = words_all[i], i + 1
                continue
            r = REDIRECT_WORD.match(w)
            if r:
                if r.end() == len(w):
                    targets.append(marked_all[i] if i < len(marked_all) else "")
                    i += 1
                else:
                    targets.append(m[r.end():])
                continue
            words.append(w)
            marked.append(m)
        k = _prog_index(words)
        c = {"prog": None, "args": [], "names": [], "globs": [], "base": cur, "wrappers": set(), "assigned": set(),
             "env_s": None, "herestring": here, "heredoc": HEREDOC_MARK in words_all, "piped": False, "targets": []}

        def note(res, m):
            if res is None:
                c["globs"].extend(_wild_glob(m, env))
                return
            for p, g in res:
                c["names"].append(p)
                if g:
                    c["globs"].append(p)
        for tm in targets:
            res = _resolve(tm, env)
            note(res, tm)
            c["targets"].extend(p for p, _ in (res or []))
        local = dict(env)
        end = k if k is not None else len(words)
        for j in range(end):
            a = re.match(r"([A-Za-z_]\w*)(\+?)=", words[j])
            if a and not c["wrappers"]:
                res = _resolve(marked[j][a.end():], env)
                note(res, marked[j][a.end():])
                c["assigned"].add(a.group(1))
                if res is None or len(res) != 1 or array_assign(words[j], text):
                    local.pop(a.group(1), None)
                else:
                    local[a.group(1)] = (local.get(a.group(1), "") if a.group(2) else "") + res[0][0]
                continue
            name = os.path.basename(words[j].lstrip("("))
            if name in WRAPPERS:
                c["wrappers"].add(name)
            if name == "env":
                for x in range(j + 1, end):
                    w = words[x]
                    d = None
                    if w in ("-C", "--chdir") and x + 1 < end:
                        d = _expand(words[x + 1], env)
                    elif w.startswith("--chdir=") or (w.startswith("-C") and len(w) > 2):
                        d = _expand(w.split("=", 1)[1] if w.startswith("--") else w[2:], env)
                    elif w.startswith("-S") and len(w) > 2:
                        c["env_s"] = w[2:]
                        continue
                    elif w.startswith("--split-string="):
                        c["env_s"] = w.split("=", 1)[1]
                        continue
                    else:
                        continue
                    c["base"] = None if d is None or (cur is None and not os.path.isabs(d)) else \
                        _norm_path(os.path.join(cur or "", d))
                if end > 0 and words[end - 1] in ("-S", "--split-string") and k is not None:
                    c["env_s"], k = words[k], None
        if k is None:  # assignments only: they stay for the rest of the line
            for n in c["assigned"]:
                if n in local:
                    env[n] = local[n]
                else:
                    env.pop(n, None)
            out.append(c)
            continue
        prog = os.path.basename(words[k].lstrip("("))
        if prog == "builtin" and k + 1 < len(words):
            k, prog = k + 1, os.path.basename(words[k + 1])
        c["prog"] = prog
        note(_resolve(marked[k], env), marked[k])
        c["piped"] = bool(re.search(r"(?<!\|)\|&?\s*(?:[A-Za-z_]\w*=\S*\s+)*" + re.escape(words[k]) + r"(?![\w.-])",
                                    text))
        for x in range(k + 1, len(words)):
            res = _resolve(marked[x], env)
            note(res, marked[x])
            c["args"].append(None if res is None else [p for p, _ in res])
        plain = [a[0] if a else None for a in c["args"]]
        if prog in ("export", "local", "declare", "typeset", "readonly"):
            for a in c["args"]:
                mm = re.match(r"([A-Za-z_]\w*)(\+?)=(.*)$", a[0], re.S) if a and len(a) == 1 else None
                if mm:
                    env[mm.group(1)] = (env.get(mm.group(1), "") if mm.group(2) else "") + mm.group(3)
                    c["assigned"].add(mm.group(1))
        elif prog in ("read", "for", "select", "unset", "mapfile", "readarray", "getopts", "printf"):
            for a in plain:
                if a and re.fullmatch(r"[A-Za-z_]\w*", a):
                    env.pop(a, None)
        elif prog in ("cd", "chdir", "pushd", "popd"):
            ops = [a for a in c["args"] if a is None or not a[0].startswith("-") or a[0] == "-" or
                   re.fullmatch(r"[+-]\d+", a[0])]
            if prog == "popd":
                new = stack.pop() if stack and not ops else None
                old, cur = cur, new
            elif prog == "pushd" and not ops:
                if stack:
                    stack[-1], cur, old = cur, stack[-1], cur
                else:
                    old, cur = cur, None
            else:
                t = ops[0] if len(ops) == 1 and ops[0] is not None and len(ops[0]) == 1 else None
                t = t[0] if t else (None if ops else local.get("HOME"))
                if t == "-":
                    new = old
                elif t is None or re.fullmatch(r"[+-]\d+", t) or ("CDPATH" in local and not t.startswith(("/", "."))):
                    new = None
                elif os.path.isabs(t):
                    new = _norm_path(t)
                else:
                    new = None if cur is None else _norm_path(os.path.join(cur, t))
                if prog == "pushd" and cur is not None:
                    stack.append(cur)
                old, cur = cur, new
        out.append(c)
    return out, parsed


def _wild_glob(marked, env):
    """Glob words for an argument with parts only the run knows, when those parts all lie in its last name: each one
    reads as * (qa/$a.env as qa/*.env, qa/$(echo s)ecrets.env as qa/*ecrets.env)."""
    w = _expand(marked, env, mark=True, wild=True)
    if not w or WILD not in w:
        return []
    folder, slash, name = w.rpartition("/")
    if not slash or WILD in folder:
        return []
    return [a.translate(_UNMARK) for a in _brace_alts(folder + slash + name.replace(WILD, GLOB_MARKS["*"]))]


def array_assign(word, text):
    """True when an empty assignment word (a=) opens an array on the line (a=(x y))."""
    m = re.fullmatch(r"([A-Za-z_]\w*)\+?=", word)
    return bool(m) and re.search(r"(?<![\w])" + re.escape(m.group(1)) + r"\+?=\(", text) is not None


def _secrets_glob(cmds):
    """True when a command names the password file once expanded, or has a glob the shell expands that can reach it:
    its last part can be secrets.env and its folder can be the workspace's qa folder, or it is bare and the command runs
    (or may run) there. An assignment's or an option's value (F=qa/s*, --x=qa/s*) counts by itself."""
    for c in cmds:
        if any(SECRETS_WORD.search(_fold(s)) for s in c["names"]):
            return True
        for g in c["globs"]:
            g = re.sub(r"^(?:[A-Za-z_]\w*\+?|--?[\w-]+)=", "", g)
            folder, slash, name = g.rpartition("/")
            if not _glob_match(SECRETS_NAME, name):
                continue
            if slash:
                if _is_qa_home(folder or "/", c["base"]):
                    return True
            elif c["base"] is None or _in_qa_home(c["base"]):
                return True
    return False


def _opts(first, values):
    """(short flags, long options) in a command's option words; a short flag in values takes a value, which ends its
    cluster (-drecurse is -d recurse) or is the next word."""
    flags, longs, skip = set(), [], False
    for a in first:
        if skip:
            skip = False
            continue
        if a == "--":
            break
        if a.startswith("--"):
            longs.append(a)
        elif a.startswith("-") and len(a) > 1:
            for k, ch in enumerate(a[1:]):
                flags.add(ch)
                if ch in values:
                    skip = k == len(a) - 2
                    break
    return flags, longs


def _positionals(args, first, values="", value_longs=()):
    """The non-option arguments (as their alternatives, or None when unknown), past option values."""
    out, i = [], 0
    while i < len(args):
        a = first[i]
        if a == "--":
            out.extend(args[i + 1:])
            break
        if args[i] is not None and a.startswith("--"):
            i += 1 + ("=" not in a and a in value_longs)
            continue
        if args[i] is not None and a.startswith("-") and len(a) > 1:
            for k, ch in enumerate(a[1:]):
                if ch in values:
                    i += k == len(a) - 2
                    break
            i += 1
            continue
        out.append(args[i])
        i += 1
    return out


def _opt_values(first, shorts=(), longs=()):
    """The values given to the named options (-x V, -xV, --long V, --long=V)."""
    out = []
    for i, a in enumerate(first):
        nxt = first[i + 1] if i + 1 < len(first) else ""
        if a in shorts or a in longs:
            out.append(nxt)
        elif any(len(s) == 2 and a.startswith(s) and len(a) > 2 and not a.startswith("--") for s in shorts):
            out.append(a[2:])
        else:
            out.extend(a.split("=", 1)[1] for lo in longs if a.startswith(lo + "="))
    return out


def _hits_secrets(pat, fold=False, rel=("secrets.env", "qa/secrets.env")):
    """True when a filter pattern matches the password file: its name, or its path from the search root."""
    for alt in _brace_plain(pat):
        alt = alt.lstrip("/")
        if "/" not in alt.rstrip("/") and _glob_match(SECRETS_NAME, alt, fold):
            return True
        if any(_parts_match(alt.rstrip("/").split("/"), r.split("/"), fold) for r in rel):
            return True
    return False


def _left_out(prog, first):
    """True when the command cannot print a line of the password file: its filters leave it out, as that program reads
    them (grep: the last matching --include/--exclude wins; rg: the last matching -g wins, a type keeps only that type;
    rsync: the first matching rule wins; tar, diff: an exclude; zip: -x, or -i lists that all miss; find: -name tests
    that all miss)."""
    if prog in GREP_PROGS:
        rules = [("in", v) for v in _opt_values(first, (), ("--include",))] + \
                [("ex", v) for v in _opt_values(first, (), ("--exclude",))]
        order = sorted(rules, key=lambda r: next((i for i, a in enumerate(first) if r[1] in a), 0))
        hit = None
        for kind, pat in order:
            if _hits_secrets(pat):
                hit = kind
        return bool(order) and (hit == "ex" or (hit is None and order[0][0] == "in"))
    if prog == "rg":
        if any(t and t != "all" for t in _opt_values(first, ("-t",), ("--type",))):
            return True
        state, includes = None, False
        for i, a in enumerate(first):
            for pat, ci in ((v, a.startswith("--iglob")) for v in _opt_values([a] + first[i + 1:i + 2], ("-g",),
                                                                             ("--glob", "--iglob"))[:1]):
                neg = pat.startswith("!")
                includes |= not neg
                if _hits_secrets(pat[neg:], ci):
                    state = "ex" if neg else "in"
        return state == "ex" or (state is None and includes)
    if prog == "rsync":
        for i, a in enumerate(first):
            if a in ("-f", "--filter") or a.startswith(("--filter=", "--exclude-from", "--include-from", "--files-from")):
                return False
            for opt, kind in (("--exclude", "ex"), ("--include", "in")):
                vals = _opt_values([a] + first[i + 1:i + 2], (), (opt,))[:1]
                if vals and _hits_secrets(vals[0]):
                    return kind == "ex"
        return False
    if prog in ("tar", "gtar", "bsdtar", "diff", "gdiff"):
        shorts = ("-x",) if prog.endswith("diff") else ()
        return any(_hits_secrets(v) for v in _opt_values(first, shorts, ("--exclude",)))
    if prog == "zip":
        lists, cur = {"-x": [], "-i": []}, None
        for a in first:
            if a in lists:
                cur = a
            elif a.startswith("-"):
                cur = None
            elif cur:
                lists[cur].append(a)
        return any(_hits_secrets(p, rel=("qa/secrets.env",)) for p in lists["-x"]) or \
            (bool(lists["-i"]) and not any(_hits_secrets(p, rel=("qa/secrets.env",)) for p in lists["-i"]))
    if prog == "find":
        if any(a in ("-o", "-or", ",", "!", "-not") for a in first):
            return False
        names = _opt_values(first, ("-name", "-iname", "-path", "-ipath", "-wholename"))
        return bool(names) and not any(_hits_secrets(n, rel=("qa/secrets.env", "./secrets.env", "secrets.env"))
                                       for n in names)
    return False


def _names_only(prog, first, flags, longs):
    """True when a search or compare prints only file names or counts, never a line of a file."""
    if any(lo in ("--json", "--vimgrep") or lo.startswith("--json") for lo in longs):
        return False
    short = {"rg": "lcq", "ag": "lcL", "ack": "lLc", "diff": "q", "gdiff": "q"}.get(prog, "lLcq" if prog in GREP_PROGS
                                                                                     else "")
    return bool(flags & set(short)) or any(lo.split("=")[0] in NAMES_ONLY_LONG for lo in longs)


def _folder_reads(c):
    """[(operand alternatives)] the command reads, copies, archives, links or serves as a whole folder, when no filter
    or names-only output keeps the password file out; [] otherwise."""
    p, args = c["prog"], c["args"]
    if not p:
        return []
    first = [a[0] if a else "" for a in args]
    default = [] if c["piped"] else [["."]]
    if p in GREP_PROGS or p in ("rg", "ag", "ack"):
        values, vlongs = (GREP_VALUES, GREP_VALUE_LONGS) if p in GREP_PROGS else (RG_VALUES, RG_VALUE_LONGS)
        flags, longs = _opts(first, values)
        rec = p not in GREP_PROGS or p == "rgrep" or bool(flags & {"r", "R"}) or \
            any(lo in ("--recursive", "--dereference-recursive", "--directories=recurse") for lo in longs) or \
            "recurse" in _opt_values(first, ("-d",), ("--directories",))
        if not rec or _names_only(p, first, flags, longs) or _left_out("rg" if p == "rg" else p, first):
            return []
        ops = _positionals(args, first, values, vlongs)
        if not (flags & {"e", "f"} or any(lo.split("=")[0] in ("--regexp", "--file") for lo in longs)
                or "--files" in longs):
            ops = ops[1:]
        return ops or default
    if p in ("cp", "gcp", "rsync", "scp", "ditto", "ln", "gln"):
        values = {"rsync": "eBTfM", "scp": "cFiJloPS"}.get(p, "tS")
        flags, longs = _opts(first, values)
        if p in ("cp", "gcp") and not (flags & {"r", "R", "a"} or any(lo in ("--recursive", "--archive")
                                                                      for lo in longs)):
            return []
        if p == "rsync" and not (flags & {"r", "a", "d"} or any(lo in ("--recursive", "--archive", "--dirs")
                                                                for lo in longs)):
            return []
        if p == "scp" and "r" not in flags:
            return []
        if _left_out(p, first):
            return []
        ops = _positionals(args, first, values, ("--rsh", "--exclude", "--include", "--filter", "--temp-dir",
                                                 "--backup-dir", "--suffix", "--chmod", "--compare-dest", "--link-dest"))
        ops = ops[:-1] if len(ops) > 1 and "-t" not in first else ops
        return [o for o in ops if o is None or not re.match(r"^[^/]*:", o[0])] if p == "scp" else ops
    if p in ("tar", "gtar", "bsdtar"):
        lead = first[0] if first and not first[0].startswith("-") and re.fullmatch(r"[A-Za-z]+", first[0]) else ""
        flags, longs = _opts(["-" + lead] + first[1:] if lead else first, "bCfIsTX")
        if not (flags & {"c", "r", "u"} or "--create" in longs or "--append" in longs) or _left_out(p, first):
            return []
        out, cdir, i, fvals = [], None, 1 if lead else 0, list(lead.count("f") * [None])
        while i < len(args):
            a = first[i]
            if a in ("-C", "--directory") and i + 1 < len(args):
                cdir, i = (args[i + 1] or [None])[0], i + 2
                continue
            if (a.startswith("-C") and len(a) > 2 and not a.startswith("--")) or a.startswith("--directory="):
                cdir, i = a.split("=", 1)[1] if a.startswith("--") else a[2:], i + 1
                continue
            if a.startswith("-") and len(a) > 1 and args[i] is not None:
                i += 1 + (not a.startswith("--") and a[-1] in "bfIsTX" and "=" not in a)
                continue
            if fvals:
                fvals.pop()
                i += 1
                continue
            alts = args[i]
            out.append(None if alts is None else
                       [os.path.join(cdir, x) if cdir and not os.path.isabs(x) else x for x in alts])
            i += 1
        return out
    if p == "zip":
        flags, longs = _opts(first, "bnOt")
        if not (flags & {"r", "R"} or any(lo in ("--recurse-paths", "--recurse-patterns") for lo in longs)) or \
                _left_out(p, first):
            return []
        ops, listing = [], False
        for a, alts in zip(first, args):
            if a in ("-x", "-i"):
                listing = True
            elif a.startswith("-"):
                listing = False
            elif not listing:
                ops.append(alts)
        return ops[1:]
    if p in ("7z", "7za") and first[:1] == ["a"]:
        return _positionals(args, first)[2:]
    if p in ("diff", "gdiff"):
        flags, longs = _opts(first, "xXSFILW")
        if not (flags & {"r"} or "--recursive" in longs) or _names_only(p, first, flags, longs) or _left_out(p, first):
            return []
        return _positionals(args, first, "xXSFILW")
    if p == "git" and "--no-index" in first:
        base = _opt_values(first, ("-C",))
        sub = next((a for a in first if not a.startswith("-") and a not in base), "")
        flags, longs = _opts(first, "eCfAB")
        if sub == "grep" and (_names_only("grep", first, flags, longs) or flags & {"l", "c", "q"}):
            return []
        ops = [o for o in _positionals(args, first, "C") if o is None or o[0] not in (sub, *base)]
        ops = ops[1:] if sub == "grep" and not flags & {"e", "f"} else ops
        if base and base[0]:
            ops = [None if o is None else [os.path.join(base[0], x) for x in o] for o in (ops or [["."]])]
        return ops or (default if sub == "grep" else [])
    if p == "find" and any(FIND_WRITES.match(a) for a in first):
        if _left_out(p, first):
            return []
        ops = []
        for a, alts in zip(first, args):
            if a.startswith(("-", "(", "!")) or a in (")",):
                break
            ops.append(alts)
        return ops or [["."]]
    if p.startswith("python") and ("http.server" in first or "SimpleHTTPServer" in first):
        dirs = _opt_values(first, ("-d",), ("--directory",))
        return [[dirs[0]]] if dirs else [["."]]
    if p.startswith("python") and any(m in first for m in ("zipfile", "tarfile")) and "-c" in first:
        return _positionals(args, first)[2:]
    if p == "hdiutil":
        return [[v] for v in _opt_values(first, ("-srcfolder", "-srcdir"))]
    if p in ("aws", "rclone", "gsutil", "azcopy"):
        return _positionals(args, first)
    return []


def _qa_dir_rule(cmds):
    """deny for a command that reads, copies, archives or serves the qa folder whole (_folder_reads), or links it or a
    folder above it."""
    for c in cmds:
        ops = _folder_reads(c)
        link = c["prog"] in ("ln", "gln")
        for o in ops:
            for x in (o or []):
                if (_reaches_qa(x, c["base"]) if link else _is_qa_home(x, c["base"])):
                    return "deny", QA_DIR_REASON
    return "pass", ""


def _shell_codes(c):
    """The lines a command runs as shell code: sh -c '...', eval, trap, env -S, a here-string to a shell, find -exec,
    su -c."""
    p, first = c["prog"], [a[0] if a else "" for a in c["args"]]
    codes = [c["env_s"]] if c["env_s"] else []
    if p in SHELL_CODE or p == "su":
        for i, a in enumerate(first):
            if a.startswith("-") and not a.startswith("--") and "c" in a[1:]:
                codes.extend(first[i + 1:i + 2])
                break
        else:
            if c["herestring"] and not any(not a.startswith("-") for a in first):
                codes.append(c["herestring"])
    elif p == "eval":
        codes.append(" ".join(first))
    elif p == "trap" and first:
        codes.append(first[0])
    elif p == "find":
        codes.extend(_find_execs(first))
    return [x for x in codes if x]


def _secrets_rule(cmd, cwd=None, depth=0):
    """deny for a line that names the password file, globs it, reads the qa folder whole, or runs such a line through a
    shell (see the comment above SECRETS_NAME); ask when it cannot be read; else pass."""
    if any(SECRETS_WORD.search(s) for s in _spellings(cmd)):
        return "deny", SECRETS_REASON
    try:
        for text in _zsh_group_texts(cmd):
            if text != cmd and any(SECRETS_WORD.search(s) for s in _spellings(text)):
                return "deny", SECRETS_REASON
            cmds, _ = _contexts(text, cwd)
            if _secrets_glob(cmds):
                return "deny", SECRETS_REASON
            d = _qa_dir_rule(cmds)
            if d[0] != "pass":
                return d
            if ARITH_CTX.search(text):  # [[ ]], (( )) and let run $(...) even from inside quotes
                for body in CMD_SUBST.findall(text):
                    d = _secrets_rule(body, cwd, depth + 1) if depth < 3 else ("ask", "shell code nested too deep for the gate to read: needs your OK")
                    if d[0] != "pass":
                        return d
            for c in cmds:
                for code in _shell_codes(c):
                    d = _secrets_rule(code, UNKNOWN_DIR if c["base"] is None else c["base"], depth + 1) \
                        if depth < 3 else ("ask", "shell code nested too deep for the gate to read: needs your OK")
                    if d[0] != "pass":
                        return d
    except Exception as exc:  # noqa: BLE001 - an unreadable line asks rather than passing unseen
        return "ask", f"the gate could not read this line for the password-file rule ({type(exc).__name__}): needs your OK"
    return "pass", ""


def _is_secrets_path(p, cwd=None):
    """True when a file tool's path is the password file: any .../qa/secrets.env (case and Unicode folded, firmlinks
    and links followed)."""
    if not p:
        return False
    full = _norm_path(p if os.path.isabs(p) else os.path.join(cwd or WORKSPACE, p))
    f = _fold(full)
    if f.endswith("/qa/" + SECRETS_NAME) or f == _fold(_norm_path(SECRETS_HOME)):
        return True
    try:
        return _fold(_norm_path(os.path.realpath(full))) == _fold(_norm_path(os.path.realpath(SECRETS_HOME)))
    except (OSError, ValueError):
        return False


def _grep_secrets(inp, cwd=None):
    """deny for a Grep on the password file, or a content-mode Grep of the qa folder itself whose glob and type leave
    the file in (the tool's default output, file names, may)."""
    path = inp.get("path") or cwd or WORKSPACE
    full = _norm_path(path if os.path.isabs(path) else os.path.join(cwd or WORKSPACE, path))
    if _is_secrets_path(full):
        return "deny", SECRETS_REASON
    if (inp.get("output_mode") or "files_with_matches") != "content" or not _in_qa_home(full):
        return "pass", ""
    if inp.get("type") and inp.get("type") != "all":
        return "pass", ""
    glob = inp.get("glob") or ""
    if glob and not glob.startswith("!") and not _hits_secrets(glob):
        return "pass", ""
    if glob.startswith("!") and _hits_secrets(glob[1:]):
        return "pass", ""
    return "deny", QA_DIR_REASON


def _host_named(text):
    """True when text names ClickUp's API host: in any of _spellings, URL-decoded, or with curl's and the shell's
    {a,b} and [a-b] globs read."""
    if not CLICKUP_HINT.search(text) and not CLICKUP_HINT.search(urllib.parse.unquote(text)):
        return False
    for s in _spellings(text)[:3]:
        for t in (s, urllib.parse.unquote(s)):
            if CLICKUP_API.search(t):
                return True
            flat = re.sub(r"\[(.)-.\]", r"\1", re.sub(r"\{([^{},]*)\}", r"\1", t))
            if CLICKUP_API.search(flat) or any(CLICKUP_API.search(a) for a in _brace_plain(flat, 32)):
                return True
    return False


def _http_writes(prog, args):
    """True when a curl or wget call may send something: a method other than GET or HEAD, data, a form, an upload, a
    config file, or wget's -e."""
    for i, a in enumerate(args):
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if a.startswith("--"):
            name = a[2:].split("=", 1)[0]
            name = name[7:] if name.startswith("expand-") else name
            val = a.split("=", 1)[1] if "=" in a else nxt
            if name in ("request", "method"):
                if val.upper() not in ("GET", "HEAD"):
                    return True
            elif name.startswith(("data", "form", "upload-file", "json", "config", "post", "body", "execute")):
                return True
        elif a.startswith("-") and len(a) > 1:
            if prog == "wget":
                if "e" in a[1:]:
                    return True
                continue
            for k, ch in enumerate(a[1:]):
                if ch == "X":
                    val = a[k + 2:] or nxt
                    if val.upper() not in ("GET", "HEAD"):
                        return True
                    break
                if ch in "dFTK":
                    return True
                if ch in CURL_VALUES:
                    break
    return False


def _cmd_sends(c, depth):
    """True when a command can send a request: see the comment above CLICKUP_API."""
    p = c["prog"]
    if c["assigned"] & INJECT_VARS:
        return True
    codes = _shell_codes(c)
    if c["env_s"]:
        return depth >= 3 or any(_line_sends(x, depth + 1) for x in codes)
    if p is None:
        return False
    first = [a[0] if a else None for a in c["args"]]
    known = [a for a in first if a is not None]
    if any(os.path.basename(t) in HTTP_CONFIGS for t in c["targets"]) or \
            (p == "tee" and any(os.path.basename(a) in HTTP_CONFIGS for a in known)):
        return True
    if p in ("curl", "wget"):
        return "xargs" in c["wrappers"] or None in first or _http_writes(p, known)
    if p in NET_CLIENTS:
        return True
    if p in SHELL_CODE or p in ("eval", "trap", "su", "find"):
        if codes:
            return depth >= 3 or any(_line_sends(x, depth + 1) for x in codes)
        if p == "find":
            return False
        return p not in ("find",) and (c["heredoc"] or c["piped"] or bool(c["herestring"]) or
                                        not any(not a.startswith("-") for a in known))
    if p == "ssh":
        ops = _positionals(c["args"], [a or "" for a in first], "BbcDEeFIiJLlmOoPpQRSWw")
        remote = " ".join(o[0] for o in ops[1:] if o)
        return bool(remote) and (depth >= 3 or _line_sends(remote, depth + 1))
    if p.startswith("python") or p in CODE_RUNNERS:
        pos = [a for a in known if not a.startswith("-")]
        if p.startswith("python") and pos and os.path.basename(pos[0]) == "act.py":
            return False  # act.py's page code is read by _act_js
        if any(_host_named(s) for a in c["args"] if a for s in a):
            return True
        if any(a in CODE_ARG_FLAGS for a in known) or (pos and pos[0] != "-"):
            return False
        return c["heredoc"] or c["piped"] or bool(c["herestring"]) or "-" in known
    if p in ("awk", "gawk", "nawk", "mawk"):
        return bool(re.search(r"system\s*\(|getline|\|", " ".join(known)))
    if p == "sort":
        return any(a.startswith("--compress-program") for a in known)
    return False


def _line_sends(cmd, depth=0, cwd=None):
    """True when a command on the line can send a request (or the line cannot be read)."""
    cmds, parsed = _contexts(cmd, cwd if cwd else UNKNOWN_DIR)
    return not parsed or bool(ZSH_QUAL_CODE.search(cmd)) or any(_cmd_sends(c, depth) for c in cmds)


def _clickup_api(cmd, cwd=None):
    """ask for a line that names api.clickup.com (also once its variables are filled in) when a command on it can send
    a request; else pass."""
    if "$" not in cmd and not CLICKUP_HINT.search(cmd) and not CLICKUP_HINT.search(urllib.parse.unquote(cmd)):
        return "pass", ""
    try:
        cmds, parsed = _contexts(cmd, cwd)
        if not (_host_named(cmd) or any(_host_named(s) for c in cmds for s in c["names"])):
            return "pass", ""
        if not parsed or ZSH_QUAL_CODE.search(cmd) or any(_cmd_sends(c, 0) for c in cmds) or \
                (ARITH_CTX.search(cmd) and any(_line_sends(b, 1) for b in CMD_SUBST.findall(cmd))):
            return "ask", CLICKUP_API_REASON
    except Exception as exc:  # noqa: BLE001
        return "ask", f"the gate could not read this line for the ClickUp rule ({type(exc).__name__}): needs your OK"
    return "pass", ""


def _approval_paths(cmd):
    """2026-11-03: a shell line that reads the approval key or writes approvals is denied. Read on the plain form
    (quotes and backslashes removed, as the shell joins a word)."""
    plain = re.sub(r"['\"\\]", "", cmd)
    cd = CD_STATE.search(plain)
    if APPROVAL_KEY in plain or _key_glob(plain) or (cd and cd.group(1) == "state") or \
            (STATE_DIR.search(plain) and RECURSIVE_READERS.search(plain)):
        return "deny", KEY_REASON
    if cd:
        return "deny", APPROVALS_REASON
    # 2026-11-03 security review: the same reader-only rule for the Touch ID prompt's build and the File ticket route,
    # and (same day) for the Post comment route and the comment poster; the app's routes and the poster also on the line
    # as typed and with URL dot segments dropped (review, same day)
    routes = _route_text(plain, cmd)
    for rx, reason in ((APPROVALS_TARGET, APPROVALS_REASON), (HELPER_BIN, HELPER_REASON)) + APP_ROUTES:
        if not rx.search(routes if (rx, reason) in APP_ROUTES else plain):
            continue
        for seg in SPLIT.split(plain):
            for m in re.finditer(r"(?<![<>])>>?\|?\s*([^\s;&|<>]+)", seg):
                if rx.search(" " + m.group(1)):
                    return "deny", reason  # a redirect into it
            toks = seg.split()
            if not toks:
                continue
            prog, args, _ = _command(toks)
            if prog not in APPROVAL_READERS or (prog == "find" and any(FIND_WRITES.match(a) for a in args)) or \
                    (prog == "rg" and any(a.startswith("--pre") for a in args)):
                return "deny", reason
    return "pass", ""


def _route_text(*texts):
    """One text the app-route and poster rules read (review 2026-11-03): each text as given, without its backslashes
    (a JS or JSON escape such as comment\\/post), and both with the URL dot segments a client drops before it sends a
    path (curl sends /api/results/./comment/post and /api/results/x/../comment/post as /api/results/comment/post)."""
    forms = []
    for t in texts:
        for s in (t, t.replace("\\", "")):
            forms.append(s)
            prev = None
            while s != prev:
                prev, s = s, DOT_SEGMENT.sub("", s)
            forms.append(s)
    return "\n".join(forms)


def _state_sweep(cmd):
    """2026-11-03 security review: find with -delete/-exec/..., or rm -r, over runner/state or a folder above it asks
    (find qa -name '*.used' -delete would let an approval file its ticket again). Checked last, so it never pre-empts a
    deny."""
    for seg in SPLIT.split(re.sub(r"['\"\\]", "", cmd)):
        prog, args, _ = _command(seg.split())
        sweep = (prog == "find" and any(FIND_WRITES.match(a) for a in args)) or \
            (prog == "rm" and any(re.match(r"^(-\w*[rR]|--recursive$)", a) for a in args))
        if sweep and any(_holds_state(a) for a in args if not a.startswith("-")):
            return "ask", STATE_SWEEP_REASON
    return "pass", ""


def _holds_state(word):
    """True when a path operand (read from the folder that holds qa/, as the gate assumes) is runner/state or a folder
    above it."""
    if word.startswith(("(", "!")) or word in (";", "+", "{}"):
        return False
    state = os.path.normpath(os.path.join(QA_DIR, "runner", "state"))
    p = os.path.normpath(os.path.join(os.path.dirname(QA_DIR), os.path.expanduser(word)))
    return state == p or state.startswith(p.rstrip("/") + "/")


def _approval_file(tool_name, tool_input):
    """2026-11-03: Read/Grep/Write/Edit/MultiEdit/NotebookEdit of the approval key, and file writes into approvals/."""
    p = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") or ""
    state = os.path.realpath(os.path.join(QA_DIR, "runner", "state"))
    real = os.path.realpath(os.path.join(WORKSPACE, p))
    if os.path.basename(p.rstrip("/")) == APPROVAL_KEY or real == os.path.join(state, APPROVAL_KEY):
        return "deny", KEY_REASON
    if tool_name == "Grep":
        glob = str(tool_input.get("glob") or "")
        if STATE_DIR.search(p.rstrip("/")) or real == state or APPROVAL_KEY in glob or (
                glob and fnmatch.fnmatchcase(APPROVAL_KEY, glob) and
                (not p or (state + "/").startswith(real.rstrip("/") + "/"))):
            return "deny", KEY_REASON
        return "pass", ""
    approvals = os.path.join(state, "approvals")
    if tool_name != "Read" and (APPROVALS_FILE_PATH.search(p) or real == approvals or real.startswith(approvals + "/")):
        return "deny", APPROVALS_REASON
    if tool_name != "Read" and HELPER_BIN.search("/" + os.path.basename(p.rstrip("/"))):
        return "deny", HELPER_REASON  # 2026-11-03 security review: the compiled Touch ID prompt
    return "pass", ""


def _ticket_tap(cmd):
    """The filing tap the approval pass reads, as (ticket_dir, target, selector): exactly one simple command
    QA_TICKET=<dir> python3 -I qa/runner/act.py <target> tap <selector> [--expect X] [--timeout N] [--find-timeout N]
    --approved, with nothing outside quotes that the shell expands, globs, redirects or separates. None otherwise."""
    quote = None
    for c in cmd:
        if c in "$`\\\n\r":
            return None
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c not in " \t" and not TICKET_PLAIN.match(c):
            return None
    try:
        cmds = _sh_scan(cmd)
    except (ValueError, IndexError, RecursionError):
        return None
    if quote or len(cmds) != 1:
        return None
    w = cmds[0]
    act = os.path.join(QA_DIR, "runner", "act.py")
    acts = {"qa/runner/act.py", os.path.join(WORKSPACE, "qa", "runner", "act.py"), act, os.path.relpath(act, WORKSPACE)}
    m = re.match(r"QA_TICKET=(.*)$", w[0], re.S)
    if not m or len(w) < 8 or w[1:3] != ["python3", "-I"] or w[3] not in acts or w[5] != "tap" or \
            not TICKET_DIR.match(m.group(1)) or w[4] not in APPROVAL_TARGETS or w[6] not in APPROVAL_SELECTORS:
        return None
    rest, approved, j = w[7:], 0, 0
    while j < len(rest):
        if rest[j] == "--approved":
            approved, j = approved + 1, j + 1
        elif rest[j] in TICKET_VALUE_OPTS and j + 1 < len(rest) and not rest[j + 1].startswith("--"):
            j += 2
        else:
            return None
    return (m.group(1), w[4], w[6]) if approved == 1 else None


def _iso(s):
    """An ISO time as an aware UTC datetime (a naive one is read as UTC). The trailing Z of approvals.py's times is read
    here, since fromisoformat before Python 3.11 (/usr/bin/python3 is 3.9) refuses it."""
    t = datetime.datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def _valid_approval(ticket_dir, target):
    """(path, id) of an approval the person gave in the app for this draft on this target that is still good now, or
    None. Good: <id>.json in <QA>/runner/state/approvals/ (no symlinks on the way), exactly the contract's fields, by
    touch_id, sig = HMAC-SHA256 with the key (its 32 bytes; the hex text as key is accepted too) over the other fields
    as compact sorted JSON, created <= now < expires <= created + 30 min, no <id>.used, and draft_sha256 equal to
    sha256(title.txt + "\\n" + description.txt) of the ticket folder now."""
    state = os.path.join(QA_DIR, "runner", "state")
    adir, keyp = os.path.join(state, "approvals"), os.path.join(state, APPROVAL_KEY)
    tdir = os.path.join(os.path.dirname(QA_DIR), ticket_dir)
    try:
        if any(os.path.islink(p) for p in (os.path.join(QA_DIR, "runner"), state, adir, keyp)):
            return None
        # 2026-11-03 security review: a ticket already filed is never filed again, even with an approval left over
        if any(os.path.lexists(os.path.join(tdir, n)) for n in ("submitted.json", "filed.json")):
            return None
        with open(keyp, encoding="ascii") as f:
            hexkey = f.read().strip()
        key = bytes.fromhex(hexkey)
        with open(os.path.join(tdir, "title.txt"), "rb") as f:
            title = f.read()
        with open(os.path.join(tdir, "description.txt"), "rb") as f:
            desc = f.read()
        names = sorted(os.listdir(adir))
    except (OSError, ValueError):
        return None
    if len(key) != 32:
        return None
    digest = hashlib.sha256(title + b"\n" + desc).hexdigest().encode("ascii")
    now = _now()
    for name in names:
        aid, ext = os.path.splitext(name)
        p = os.path.join(adir, name)
        if ext != ".json" or not APPROVAL_ID.match(aid) or os.path.islink(p) or \
                os.path.lexists(os.path.join(adir, aid + ".used")):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                a = json.load(f)
            msg = json.dumps({k: v for k, v in a.items() if k != "sig"}, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
            created, expires = _iso(a["created"]), _iso(a["expires"])
            sig, draft = a["sig"].encode("utf-8"), a["draft_sha256"].encode("utf-8")
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
        if set(a) != APPROVAL_FIELDS or a["id"] != aid or a["ticket_dir"] != ticket_dir or a["target"] != target or \
                a["by"] != "touch_id":
            continue
        if not any(hmac.compare_digest(sig, hmac.new(k, msg, hashlib.sha256).hexdigest().encode("ascii"))
                   for k in (key, hexkey.encode("ascii"))):
            continue
        if not hmac.compare_digest(draft, digest):
            continue
        if created <= now + datetime.timedelta(seconds=60) and now < expires and expires - created <= APPROVAL_TTL:
            return p, aid
    return None


def _mark_used(path, aid):
    """Write <id>.used next to the approval, atomically and only once (a hard link that fails when it exists): False
    when it is there already or cannot be written."""
    used = os.path.join(os.path.dirname(path), aid + ".used")
    tmp = f"{used}.{os.getpid()}.tmp"
    try:
        with open(tmp, "x", encoding="utf-8") as f:
            json.dump({"id": aid, "used": _now().strftime("%Y-%m-%dT%H:%M:%SZ"), "by": "gate"}, f)
        os.link(tmp, used)
        return True
    except OSError:
        return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _vcs(cmd, depth=0):
    """G1 for a shell line: clone writes, git and gh in any simple command, also inside sh -c, eval and find -exec."""
    if CLONE_SHELL_WRITE.search(cmd):
        return "deny", CLONE_REASON
    decision, reason = "pass", ""
    for toks in _segments(cmd):
        prog, args, wrappers = _command(toks)
        if prog == "find" and _find_writes_clone(args):
            return "deny", CLONE_REASON
        for inner in (_inner(prog, args) if depth < 3 else []):
            d, r = _vcs(inner, depth + 1)
            if d == "deny":
                return d, r
            if d == "ask":
                decision, reason = d, r
        if prog == "gh":
            return "deny", "gh is not used here: GitHub stays read-only, and only through git over SSH (G1)"
        if prog == "git":
            d, r = _git(args, stdin="xargs" in wrappers)
            if d == "deny":
                return d, r
            if d == "ask":
                decision, reason = d, r
    return (decision, reason) if decision != "pass" else _unseen_write(cmd)


def _bash(cmd, cwd=None):
    """Today's decision, except the one approval pass (2026-11-03): the filing tap of a ticket the person approved in
    the app, when the line passes every other rule (the rules read again without the --approved ask). The Next tap
    uses the approval up."""
    today = _bash_rules(cmd, cwd=cwd)
    if today[0] != "ask":
        return today
    tap = _ticket_tap(cmd)
    if not tap or _bash_rules(cmd, skip_act=True, cwd=cwd)[0] != "pass":
        return today
    found = _valid_approval(tap[0], tap[1])
    if not found or (tap[2] == "text:Next" and not _mark_used(*found)):
        return today
    return "pass", f"{tap[2]} on {tap[1]} for {tap[0]}: approved in the app with Touch ID ({found[1]})"


def _bash_rules(cmd, skip_act=False, cwd=None):
    """Every shell rule; a direct ClickUp API call (2026-11-04) asks last, so it never pre-empts a deny. cwd is the
    hook's working folder, when the hook gives one."""
    decision = _shell_rules(cmd, skip_act, cwd)
    return _clickup_api(cmd, cwd) if decision[0] == "pass" else decision


def _shell_rules(cmd, skip_act=False, cwd=None):
    if not cmd:
        return "pass", ""
    vcs = _vcs(cmd)
    if vcs[0] == "deny":
        return vcs
    secrets = _secrets_rule(cmd, cwd)
    if secrets[0] != "pass":
        return secrets
    approval = _approval_paths(cmd)
    if approval[0] != "pass":
        return approval
    # the write and its target stand in one command: a newline ends it as ; does, a backslash-newline does not (critic J:
    # a >/dev/null at the end of one line no longer reaches a qa/runs/ path on a later line)
    if re.search(r"(^|\s)(>>?|rm\s|mv\s|sed\s+-i|truncate\s|tee\s)(?:[^;|&\n]|\\\n)*qa/(ledger|cases|runs)/", cmd):
        return "deny", "records and the ledger are append-only: write a superseding record through qa/runner instead"
    if CDP_INPUT.search(cmd) and not ALLOWED_DRIVERS.search(cmd):
        return "deny", "raw CDP Input events are not allowed: act.py is the only sender of input"
    if OLD_HELPERS.search(cmd):
        return "deny", "the old tap/CDP helpers are retired: drive devices only through qa/runner (act.py)"
    if re.search(r"\bQA_STANDING_STEP\b", cmd):
        return "deny", "QA_STANDING_STEP is set only by run.py, for a step its standing rule allows (piece 5)"
    if re.search(r"clickup_upload\.py", cmd):
        return "ask", "uploads a file to ClickUp (a ClickUp write)"
    for check in ((_safety_write,) if skip_act else (_act_call, _safety_write)):
        d, r = check(cmd)
        if d != "pass":
            return d, r
    decision, reason, runs_unserialed = "pass", "", None
    test_push = _test_file_push(cmd, cwd)  # 2026-11-16: the whole command is plain test-file pushes into QA folders
    for args, has_serial in _adb_segments(cmd):
        words = args.split()
        at = _adb_command_at(words)
        # 2026-11-16: past adb's global options; when one is not read, the rules are tried at every word
        for at in ([at] if at is not None else range(max(len(words), 1))):
            a = " ".join(words[at:])
            hit = _adb_rule(a, test_push)
            if hit and hit[0] == "deny":
                return hit
            if not has_serial and not ADB_NO_DEVICE.match(a):
                # only when the line runs adb as a program somewhere without -s: a mention in text is not a call
                runs_unserialed = _runs_adb_unserialed(cmd) if runs_unserialed is None else runs_unserialed
                if runs_unserialed:
                    return "deny", "every adb call needs -s <serial> (several tablets are attached)"
            if hit:
                decision, reason = hit
    # a run the mention scan above cannot see (adb>out, \adb, a program word set to adb at run time)
    if runs_unserialed is None and _runs_adb_unserialed(cmd):
        return "deny", "every adb call needs -s <serial> (several tablets are attached)"
    # 2026-11-16/17 (the person's OK, "Yes, fix them"): the same rules again on each adb call the line really runs, read
    # as the shell reads it (a line continuation or a wrapper cannot hide the command there), plus ADB_REAL_CALL_ASK; such
    # a call with a global option the gate does not read, or a command word computed at run time ($C, $(...), a glob),
    # asks; and so does any adb call on a line with $'...' or $"..." quoting (it can spell any word or character)
    try:
        calls = _adb_calls(cmd)
    except Exception:  # noqa: BLE001 - a scan the gate cannot finish keeps the old reading
        calls = _old_reading(cmd, cmd)
    for args, _ in calls:
        words = args.split()
        at = _adb_command_at(words)
        for k in ([at] if at is not None else range(max(len(words), 1))):
            # only the rules anchored at adb's command: a quoted tablet pipeline is one word here, so word-anywhere rules
            # (reboot, install, input tap in a grep pattern) are left to the mention scan above and the tablet line below
            a = " ".join(words[k:])
            hit = _adb_rule(a, test_push, anchored=True) or next(
                (("ask", f"adb {what}: needs your OK") for rx, what in ADB_REAL_CALL_ASK if rx.match(a)), None)
            if hit and hit[0] == "deny":
                return hit
            if hit and decision == "pass":
                decision, reason = hit
        computed = at is None or at < len(words) and (
            ADB_COMPUTED_WORD.search(words[at])
            # _ is a $(...) the gate cannot read, or words xargs adds; alone it is also a mere mention in unread text
            or words[at] == "_" and (len(words) > at + 1 or re.search(r"\$\(|`", cmd) or ADB_FROM_XARGS.search(cmd)))
        if decision == "pass" and computed:
            decision, reason = "ask", ADB_UNREAD_REASON
    if calls and decision == "pass" and ADB_FROM_XARGS.search(cmd) and _xargs_adb_risky(cmd):
        decision, reason = "ask", ("xargs runs adb with words from its input (echo ... | xargs -I{} adb ... {}), which the "
                                   "gate does not read: needs your OK")
    if calls and decision == "pass" and _has_dollar_quote(cmd):
        decision, reason = "ask", ("adb on a line with $'...' or $\"...\" quoting, which can spell any word or character "
                                   "the gate does not read: needs your OK")
    if decision != "pass":
        return decision, reason
    rest, mac_dollar = [], []
    for body_i, body in enumerate(_device_shells(cmd, rest, mac_dollar)):
        # 2026-11-16 (the person's OK, "Yes, fix them"): a shell with no command asks; the write rules are read on the
        # line as typed and with its quotes and backslashes dropped ('r'm, r\m), a redirect outside single quotes; the
        # programs it runs where a command starts; and a $ or backtick (a value filled in at run time) makes the rest of
        # the line count too
        if not body.strip():
            return "ask", ("adb shell with no command runs on the tablet whatever this Mac pipes or redirects into it: "
                           "needs your OK")
        squeezed, unquoted = re.sub(r"['\"\\]", "", body), _outside_single_quotes(body)
        # 2026-11-17: text a shell runs as commands, wherever it stands (logwrapper sh -c '... > f'), and an awk print
        # into a file or a pipe behind any wrapper; after this, single-quoted text is plain text
        awk_text = re.sub(r"['\"\\]", "", re.sub(r'"(?:\\.|[^"\\])*"', '""', body))  # awk's own "..." strings are text
        if DEVICE_RUNS_TEXT.search(squeezed) or re.search(r"\b[gn]?awk\b.*\bprintf?\b[^;}]*[|>]", awk_text):
            return "ask", ("adb shell runs text as commands on the tablet (sh -c, awk system(), getline or print into a "
                           "file), which the gate cannot read: needs your OK")
        for rx, what in DEVICE_WRITES:
            if (rx.search(unquoted) if rx is DEVICE_REDIRECT else rx.search(body) or rx.search(squeezed)):
                return "ask", f"adb shell {what}: needs your OK"
        risk = _device_program_risk(body)
        if risk:
            return risk
        # a value this Mac fills in ($X, $(...), not quoted '...') is joined by adb into the tablet line and read again
        # there, so it can carry ; > rm: the line's assignments, loop lists, here-strings and $(...) / backtick texts
        # are read for them (after the review of 2026-11-17: the whole line was read, also for $ the tablet fills in,
        # so this Mac's own | tee, awk NR>1 or an echo with -> asked)
        if mac_dollar[body_i] if body_i < len(mac_dollar) else re.search(r"[$`]", body):
            carried = _mac_values(cmd)
            if any(rx.search(carried) for rx, _ in DEVICE_WRITES) or re.search(r"[;&|<>\n]", carried):
                return "ask", ("adb shell with a value filled in at run time whose text holds a separator or a write "
                               "(; | > rm cp tee ...): the value could carry it to the tablet; needs your OK")
    # ssh to webpc044: tunnel and status reads pass, anything else asks
    m = re.search(r"\bssh\b((?:\s+-\S+(?:\s+(?!webpc044\b)[^\s-]\S*)?)*)\s+webpc044\b(.*)$", cmd, re.S)
    if m:
        opts, remote = m.group(1), m.group(2).strip().strip("'\"")
        if re.search(r"-N\b", opts) and re.search(r"-L\s*9350:127\.0\.0\.1:9222\b", opts) and not remote:
            return "pass", ""
        if remote and not all(WEBPC044_READS.match(part) for part in re.split(r"\s*(?:;|&&|\|\||\|)\s*", remote) if part.strip()):
            return "ask", "runs a command on webpc044 that is not a status read"
        if not remote:
            return "ask", "opens an interactive shell on webpc044"
    if vcs[0] == "pass":
        # 2026-11-03 security review: last, so they never pre-empt a deny; _act_js also holds on the approval pass
        for check in (_act_js, _state_sweep):
            d, r = check(cmd)
            if d != "pass":
                return d, r
    return vcs


def decide(tool_name, tool_input, cwd=None):
    tool_input = tool_input or {}
    if tool_name == "Bash":
        return _bash(tool_input.get("command", ""), cwd if isinstance(cwd, str) else None)
    if tool_name.startswith("mcp__"):
        # 2026-11-03 security review: no browser or connector call to File ticket; same day, nor to Post comment or the
        # comment poster (review, same day: also with backslashes and URL dot segments dropped)
        text = _route_text(json.dumps(tool_input))
        hit = next((reason for rx, reason in APP_ROUTES if rx.search(text)), None)
        if hit:
            return "deny", hit
    if tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Grep"):
        approval = _approval_file(tool_name, tool_input)
        if approval[0] != "pass":
            return approval
        if tool_name == "Grep":
            return _grep_secrets(tool_input, cwd if isinstance(cwd, str) else None)  # 2026-11-04
        p = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        rel = os.path.relpath(p, WORKSPACE) if os.path.isabs(p) else p
        # 2026-11-04: case ignored (this disk ignores it), folders like qa/app/.. read, links followed
        if _is_secrets_path(p, cwd if isinstance(cwd, str) else None):
            return "deny", "qa/secrets.env is never read or written by the model; only qa/runner reads it"
        if tool_name != "Read" and CLONE_PATH.search(p):
            return "deny", "the WingtraCloud and WingtraPilot clones are read-only (G1)"
        if tool_name != "Read" and RECORD_PATH.search(rel) and os.path.exists(os.path.join(WORKSPACE, rel)):
            return "deny", "hashed records and the ledger are immutable: write a superseding record through qa/runner"
        if tool_name != "Read" and ((not rel.startswith("..") and SAFETY_REL.match(rel)) or SAFETY_HOME.match(p)):
            return "ask", SAFETY_REASON
        return "pass", ""
    if "clickup_" in tool_name:
        op = tool_name.split("__")[-1]
        if CLICKUP_COMMENT.search(op):
            return "deny", "ClickUp comments and chat messages are drafted locally; you post them"
        if op == "clickup_execute_operator":
            pair = f"{tool_input.get('model') or '?'}.{tool_input.get('operator') or '?'}"
            if pair in CLICKUP_OPERATOR_READS:
                return "pass", ""
            return "ask", f"ClickUp operator {pair} is not on the read allow-list"
        if CLICKUP_READ.search(op):
            return "pass", ""
        return "ask", f"{op} writes to ClickUp"
    op = tool_name.split("__")[-1]
    if tool_name.startswith("mcp__") and not INTERNAL_MCP.match(tool_name):
        if op in GITHUB_WRITES:
            return "deny", f"{op} writes to GitHub: GitHub stays read-only (G1)"
        if WRITE_SHAPED.match(op):
            return "ask", f"{op} looks like a write to an external resource the gate does not know"
    # 2026-11-04: a connector or browser call naming ClickUp's API (the ClickUp tools returned above, by their rules)
    if NETWORK_MCP.match(tool_name) and _host_named(_route_text(json.dumps(tool_input))):
        return "ask", CLICKUP_API_REASON
    return "pass", ""
