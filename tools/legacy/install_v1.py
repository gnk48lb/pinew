#!/usr/bin/env python3
"""把 pi 的 v1 配置包装进你的 pi 配置目录。装的就是 round4 实测过的那一份文件，逐字节核对。

    python3 install_v1.py --dry-run    只显示会发生什么，不改任何东西（先跑这个）
    python3 install_v1.py              安装；会被替换的文件先存进备份
    python3 install_v1.py --status     现在装了什么、有没有被你改过
    python3 install_v1.py --undo       撤销最近一次安装（只动它装的东西，你后来改过的文件不碰）

只用 Python 标准库。只读写 pi 配置目录（默认 ~/.pi/agent，或环境变量 PI_CODING_AGENT_DIR，或 --dest）。
从不碰 models.json、auth.json、sessions/ 和任何不在包里的文件。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# 原样复制进配置目录的文件（相对路径）。settings.json 单独处理：逐个键合并，你的其他键保留。
FILES = [
    "AGENTS.md", "presets.json",
    "extensions/safety-gate.ts", "extensions/budget-guard.ts", "extensions/preset.ts",
    "prompts/init.md", "prompts/plan.md", "prompts/review.md", "prompts/commit.md", "prompts/explain.md",
    "skills/debug/SKILL.md", "skills/refactor/SKILL.md",
]
SETTINGS = "settings.json"
BACKUP_PREFIX = ".v1-backup-"
MANIFEST = "MANIFEST.json"
UNDONE = "UNDONE"
TMP_PREFIX = ".v1tmp-"

# round4 实测的 v1.2 包的 sha256（构建时写入）。包被改过就拒绝安装，除非加 --allow-modified-pack。
EXPECTED = {
    "AGENTS.md": "251a18c75178b893ced107bb13614c601bea2825dea27a6e7f00688772132b6c",
    "presets.json": "5a7ef03aa594e59d193084ef171a584d701615f559b166cbb0e709f924732d5a",
    "extensions/safety-gate.ts": "902abdc9d5ba32ffc3a6eed82278f261213834f6793b1c2390a1676d4bfeb902",
    "extensions/budget-guard.ts": "ea8486cc3d610a91c66cacc1aced8a116efd94086a418d1c98498d835f2bd756",
    "extensions/preset.ts": "7942b7f933f97bcc29024a87f8526969413ba6f87b0bec537648a2926a0a4e29",
    "prompts/init.md": "ab24f1f38b85558e51f081ea1f33459d668e375cd673d46029430be3c49f5b64",
    "prompts/plan.md": "76aa1b3557bb65d962d6ca702643aa5b53f46aa361f6f8a621cb359c5165baf6",
    "prompts/review.md": "b88ace16dc070d9ec7b6255f292aaa0a1f9e8b5244f7013df869808326deea0a",
    "prompts/commit.md": "a025d3eab67341a6be4f258bc96d6bc8bc0a2cfc4240bd7f8764449b54350805",
    "prompts/explain.md": "6dc897ec5a9c9742f8872fdcba82a64bfbf411097409c7c2272f4e2bb856150f",
    "skills/debug/SKILL.md": "0ea116786974e8d4ebe27e7848ceed1942467bbd12e4ffbfbef4a08154154c0c",
    "skills/refactor/SKILL.md": "1c08d0a3b26ac576a01b98b116af147cc6cf6d44537463f65f633e63b1398ac6",
    "settings.json": "75b1471c72d507b110c51ddc530406478db919c228d77e4a56735bc4c7c65fc1",
}


class Stop(Exception):
    """一个已经讲清楚原因的失败；不打印 traceback，退出码 2。"""


def say(*a):
    print(*a)


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(path):
    with open(path, "rb") as f:
        return sha_bytes(f.read())


def pack_dir(args):
    for cand in ([args.pack] if args.pack else [os.path.join(HERE, "v1"), os.path.join(os.path.dirname(HERE), "v1")]):
        if cand and os.path.isfile(os.path.join(cand, "AGENTS.md")):
            return os.path.abspath(cand)
    raise Stop("找不到 v1 包（要有一个含 AGENTS.md 的 v1 文件夹，放在本脚本旁边，或用 --pack 指定）")


def agent_dir(args):
    raw = args.dest or os.environ.get("PI_CODING_AGENT_DIR") or os.path.join("~", ".pi", "agent")
    return os.path.abspath(os.path.expanduser(raw))


def check_pack(pack, allow_modified):
    bad = []
    for rel in FILES + [SETTINGS]:
        p = os.path.join(pack, rel)
        if not os.path.isfile(p):
            bad.append("缺少 " + rel)
        elif EXPECTED and sha_file(p) != EXPECTED.get(rel):
            bad.append("和实测版本不一致 " + rel)
    if bad and not allow_modified:
        raise Stop("这个包不是 round4 实测的那一份：\n  " + "\n  ".join(bad) +
                   "\n如果是你特意改的，加 --allow-modified-pack 继续（那就不再是“实测过的 v1”了）。")
    return bad


def parse_settings(path):
    """pi 自己读 settings.json 用的是严格 JSON（只去掉开头的 BOM）；这里同样严格，空文件当作 {}。"""
    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig")
    if not text.strip():
        return {}
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise Stop("你的 %s 不是合法的 JSON（%s）。pi 自己也读不了它。请先修好，或把它改名移开，再运行本脚本。" % (path, e))
    if not isinstance(obj, dict):
        raise Stop("你的 %s 的最外层不是 { }。请先修好它再运行本脚本。" % path)
    return obj


def write_atomic(path, data, like=None):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if like and os.path.exists(like):
            shutil.copymode(like, tmp)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def copy_atomic(src, dst):
    """备份用：目标要么不存在、要么完整（不会留下写了一半的文件）；保留权限和修改时间。"""
    with open(src, "rb") as f:
        data = f.read()
    write_atomic(dst, data)
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass                                    # 时间戳只是锦上添花


def restore_file(backup_path, target):
    """把备份里的文件原样放回去：内容、权限、修改时间。"""
    with open(backup_path, "rb") as f:
        data = f.read()
    write_atomic(target, data, like=backup_path)
    try:
        st = os.stat(backup_path)
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass


def sweep_leftovers(dest):
    """清掉上次被强行打断（断电、kill -9）时可能留下的临时文件和空的备份文件夹。只认本脚本自己的名字。"""
    removed = []
    if not os.path.isdir(dest):
        return removed
    dirs = {dest} | {os.path.join(dest, os.path.dirname(r)) for r in FILES}
    backup_dirs = [os.path.join(dest, n) for n in os.listdir(dest) if n.startswith(BACKUP_PREFIX) and os.path.isdir(os.path.join(dest, n))]
    for b in backup_dirs:                       # 备份文件夹里面（包括子文件夹）也找
        for dp, _dns, _fns in os.walk(b):
            dirs.add(dp)
    for d in sorted(dirs):
        if not os.path.isdir(d):
            continue
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if n.startswith(TMP_PREFIX) and os.path.isfile(p) and not os.path.islink(p):
                try:
                    os.unlink(p)
                    removed.append(os.path.relpath(p, dest))
                except OSError:
                    pass
    for b in backup_dirs:
        if not os.path.exists(os.path.join(b, MANIFEST)):
            try:
                os.rmdir(b)                     # 只有空文件夹才删得掉；里面有东西就留着
                removed.append(os.path.relpath(b, dest))
            except OSError:
                pass
    return removed


def settings_bytes(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------------------------------------- 计划
def make_plan(pack, dest):
    """只读：算出每个文件会怎样，以及会挡住安装的问题和需要你知道的提醒。不写任何东西。"""
    plan = {"files": [], "settings": None, "problems": [], "warnings": [], "dest_exists": os.path.isdir(dest)}
    if os.path.exists(dest) and not os.path.isdir(dest):
        plan["problems"].append("%s 存在但不是文件夹" % dest)
        return plan
    try:
        inside = os.path.commonpath([dest, pack]) == pack
    except ValueError:
        inside = False
    if inside:
        plan["problems"].append("目标文件夹在包文件夹里面（%s），这不可能是你的 pi 配置目录" % dest)
        return plan
    if plan["dest_exists"] and not os.access(dest, os.W_OK):
        plan["problems"].append("没有写入 %s 的权限" % dest)
    for rel in FILES:
        src, dst = os.path.join(pack, rel), os.path.join(dest, rel)
        item = {"rel": rel, "src": src, "dst": dst, "sha": sha_file(src)}
        if os.path.islink(dst):
            plan["problems"].append("%s 是符号链接（指向 %s）；本脚本不碰链接，请先处理它" % (dst, os.readlink(dst)))
            item["kind"] = "blocked"
        elif os.path.isdir(dst):
            plan["problems"].append("%s 是一个文件夹，应该是文件" % dst)
            item["kind"] = "blocked"
        elif not os.path.exists(dst):
            item["kind"] = "add"
        elif sha_file(dst) == item["sha"]:
            item["kind"] = "same"
        else:
            item["kind"] = "replace"
        parent = os.path.dirname(dst)
        probe = parent
        while probe and not os.path.exists(probe):
            probe = os.path.dirname(probe)
        if probe and os.path.exists(probe) and not os.path.isdir(probe):
            plan["problems"].append("%s 是文件，但需要它是文件夹" % probe)
        plan["files"].append(item)
    sp = os.path.join(dest, SETTINGS)
    new = parse_settings(os.path.join(pack, SETTINGS))
    if os.path.islink(sp):
        plan["problems"].append("%s 是符号链接；本脚本不碰链接，请先处理它" % sp)
        old, existed = {}, False
    elif os.path.isfile(sp):
        old, existed = parse_settings(sp), True       # 不合法的 JSON 在这里就停下，什么都还没改
    else:
        old, existed = {}, False
    changes = {k: {"had": k in old, "old": old.get(k), "new": v} for k, v in new.items() if not (k in old and old[k] == v)}
    plan["settings"] = {"path": sp, "existed": existed, "old": old, "merged": {**old, **new}, "changes": changes}
    ag = next((i for i in plan["files"] if i["rel"] == "AGENTS.md"), None)
    if ag and ag["kind"] == "replace":
        n_old = len(open(ag["dst"], encoding="utf-8", errors="replace").read().splitlines())
        n_new = len(open(ag["src"], encoding="utf-8", errors="replace").read().splitlines())
        plan["warnings"].append("你已经有自己的 AGENTS.md（%d 行），安装会用包里的（%d 行）替换它；你的旧文件原样存进备份，"
                                "--undo 可以还原。想保留你自己的规则，就先别装，或装完后手工把它们并进新的 AGENTS.md。" % (n_old, n_new))
    if plan["dest_exists"] and not os.path.exists(os.path.join(dest, "models.json")):
        plan["warnings"].append("%s 里没有 models.json：这里可能不是你 pi 实际用的配置目录。" % dest)
    return plan


def show_plan(plan, dest, dry):
    n = {k: sum(1 for i in plan["files"] if i["kind"] == k) for k in ("add", "replace", "same", "blocked")}
    label = {"add": "新增", "replace": "替换", "same": "相同", "blocked": "受阻"}
    say("")
    say("会发生的事%s：" % ("（--dry-run，不会改任何东西）" if dry else ""))
    for i in plan["files"]:
        say("  %-4s %s" % (label[i["kind"]], i["rel"]))
    ch = plan["settings"]["changes"]
    if ch:
        say("  合并 settings.json：" + "；".join("%s：%s → %s" % (k, json.dumps(c["old"], ensure_ascii=False) if c["had"] else "（没有）",
                                                            json.dumps(c["new"], ensure_ascii=False)) for k, c in ch.items()))
    else:
        say("  相同 settings.json（包里的键你已经都有了）")
    say("  不动 models.json、auth.json、sessions/ 和所有不在包里的文件（包括你自己的扩展、技能、提示）")
    say("")
    say("合计：新增 %d、替换 %d、相同 %d；settings.json 合并 %d 个键。" % (n["add"], n["replace"], n["same"], len(ch)))
    for w in plan["warnings"]:
        say("提醒：" + w)


# ---------------------------------------------------------------------------------------------------------- 安装
def install(pack, dest, args):
    plan = make_plan(pack, dest)
    say("包  ：%s" % pack)
    say("目标：%s%s" % (dest, "" if plan["dest_exists"] else "（还不存在）"))
    if plan["problems"]:
        raise Stop("不能安装：\n  " + "\n  ".join(plan["problems"]))
    if not plan["dest_exists"] and not args.create:
        raise Stop("pi 的配置目录 %s 还不存在。如果你的 pi 把配置放在别处，请设置 PI_CODING_AGENT_DIR 或用 --dest 指定；"
                   "如果这确实是一台全新的机器，加 --create。" % dest)
    show_plan(plan, dest, args.dry_run)
    todo = [i for i in plan["files"] if i["kind"] in ("add", "replace")]
    if not todo and not plan["settings"]["changes"]:
        say("\n全部已经是最新，没有要做的事。")
        return 0
    if args.dry_run:
        say("\n（这是 --dry-run：什么都没有改。去掉 --dry-run 才会真的安装。）")
        return 0

    for r in sweep_leftovers(dest):
        say("清理了上次被打断时留下的残渣：%s" % r)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(dest, BACKUP_PREFIX + stamp)
    k = 1
    while os.path.exists(backup):
        k += 1
        backup = os.path.join(dest, "%s%s-%d" % (BACKUP_PREFIX, stamp, k))
    created_dirs = []
    os.makedirs(dest, exist_ok=True)
    os.makedirs(backup)
    st = plan["settings"]
    manifest = {"schema": 1, "state": "in-progress", "time": stamp, "pack": pack, "agent_dir": dest,
                "added": [], "replaced": [], "created_dirs": created_dirs,
                "settings": {"existed": st["existed"], "new_sha": None,
                             "changes": {k_: c for k_, c in st["changes"].items()}}}

    def save_manifest():
        write_atomic(os.path.join(backup, MANIFEST), (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

    save_manifest()                                                           # 先记账：中途断了也能 --undo
    for i in todo:                                                            # 先备份所有要被替换的旧文件
        if i["kind"] == "replace":
            b = os.path.join(backup, i["rel"])
            os.makedirs(os.path.dirname(b), exist_ok=True)
            copy_atomic(i["dst"], b)
    if st["existed"] and st["changes"]:
        copy_atomic(st["path"], os.path.join(backup, SETTINGS))
    for i in todo:
        d = os.path.dirname(i["dst"])
        missing = []
        while d and not os.path.isdir(d):
            missing.append(d)
            d = os.path.dirname(d)
        for m in reversed(missing):
            created_dirs.append(os.path.relpath(m, dest))
        (manifest["added"] if i["kind"] == "add" else manifest["replaced"]).append({"rel": i["rel"], "sha": i["sha"]})
        save_manifest()                                                       # 先记下“要建这些文件夹、要写它了”，再动手；中途断在哪一步都能 --undo
        for m in reversed(missing):
            os.mkdir(m)
        with open(i["src"], "rb") as f:
            write_atomic(i["dst"], f.read(), like=i["dst"])
    if st["changes"]:
        data = settings_bytes(st["merged"])
        manifest["settings"]["new_sha"] = sha_bytes(data)
        save_manifest()
        write_atomic(st["path"], data, like=st["path"])
    manifest["state"] = "installed"
    save_manifest()

    bad = verify(pack, dest, plan)                                            # 装完逐个读回核对
    say("")
    if bad:
        say("核对失败：\n  " + "\n  ".join(bad))
        say("可以用 --undo 撤销。")
        return 1
    say("安装完成，已逐个读回核对。")
    if any(i["kind"] == "replace" for i in todo) or (st["existed"] and st["changes"]):
        say("被替换的旧文件存在：%s" % backup)
    say("撤销：python3 %s --undo" % os.path.basename(sys.argv[0]))
    say("请关掉正在运行的 pi，重新打开它，新配置才会生效。")
    return 0


def verify(pack, dest, plan):
    bad = []
    for i in plan["files"]:
        if not os.path.isfile(i["dst"]) or sha_file(i["dst"]) != i["sha"]:
            bad.append("%s 读回来和包里的不一致" % i["rel"])
    try:
        cur = parse_settings(plan["settings"]["path"])
        for k, v in parse_settings(os.path.join(pack, SETTINGS)).items():
            if cur.get(k) != v:
                bad.append("settings.json 的 %s 不对" % k)
        for k, v in plan["settings"]["old"].items():
            if k not in parse_settings(os.path.join(pack, SETTINGS)) and cur.get(k) != v:
                bad.append("settings.json 里你原有的键 %s 变了" % k)
    except Stop as e:
        bad.append("装完后 settings.json 读不了：%s" % e)
    return bad


# ---------------------------------------------------------------------------------------------------------- 状态、撤销
def backups(dest):
    if not os.path.isdir(dest):
        return []
    out = []
    for n in sorted(os.listdir(dest)):
        if n.startswith(BACKUP_PREFIX) and os.path.isfile(os.path.join(dest, n, MANIFEST)):
            out.append(n)
    return out


def status(pack, dest):
    say("包  ：%s" % pack)
    say("目标：%s%s" % (dest, "" if os.path.isdir(dest) else "（不存在）"))
    say("")
    word = {"same": "已装，和包一致", "differs": "已装，但和包不同（你改过，或是别的版本）", "missing": "没装"}
    for rel in FILES:
        src, dst = os.path.join(pack, rel), os.path.join(dest, rel)
        k = "missing" if not os.path.isfile(dst) else ("same" if sha_file(dst) == sha_file(src) else "differs")
        say("  %-26s %s" % (rel, word[k]))
    sp = os.path.join(dest, SETTINGS)
    try:
        cur = parse_settings(sp) if os.path.isfile(sp) else {}
        new = parse_settings(os.path.join(pack, SETTINGS))
        for k, v in new.items():
            say("  settings.json 的 %-22s %s" % (k, "= %s（和包一致）" % json.dumps(cur[k]) if k in cur and cur[k] == v else
                                                  ("= %s（和包不同）" % json.dumps(cur[k]) if k in cur else "没设")))
    except Stop as e:
        say("  settings.json：" + str(e))
    say("")
    bk = backups(dest)
    if not bk:
        say("没有安装记录。")
    for n in bk:
        m = json.load(open(os.path.join(dest, n, MANIFEST), encoding="utf-8"))
        undone = os.path.exists(os.path.join(dest, n, UNDONE))
        say("安装记录 %s：新增 %d、替换 %d；%s%s" % (m["time"], len(m["added"]), len(m["replaced"]),
                                                     "状态 " + m["state"], "，已撤销" if undone else ""))
    return 0


def plan_settings_undo(m, dest, bdir):
    """只读：算出 settings.json 该怎么撤销。算不出来（比如你装完后把它改成了坏 JSON）就在这里 Stop，那时什么都还没动。
    返回 (动作, 数据, 说明行)：动作是 none / restore / delete / write。"""
    ch = m["settings"]["changes"]
    if not ch:
        return ("none", None, [])
    existed = m["settings"]["existed"]
    sp, sb = os.path.join(dest, SETTINGS), os.path.join(bdir, SETTINGS)
    cur_sha = sha_file(sp) if os.path.isfile(sp) else None
    orig_sha = sha_file(sb) if existed and os.path.isfile(sb) else None
    new_sha = m["settings"].get("new_sha")
    if orig_sha is not None and cur_sha == orig_sha:
        return ("none", None, ["  本来就是原样  settings.json"])
    if cur_sha is None:
        return ("none", None, ["  settings.json 现在不存在，没有要还原的"])
    if new_sha is not None and cur_sha == new_sha:                    # 装完之后没有人再动过它：整个文件原样还原
        if orig_sha is not None:
            return ("restore", sb, ["  还原      settings.json（整个文件原样）"])
        if not existed:
            return ("delete", None, ["  删除      settings.json（它是安装时新建的）"])
    cur = parse_settings(sp)                                          # 装完之后又被改过（比如 pi 自己写过）：只还原包动过的键
    msgs, touched = [], False
    for k, c in ch.items():
        if k in cur and cur[k] == c["new"]:
            if c["had"]:
                cur[k] = c["old"]
            else:
                del cur[k]
            msgs.append("  还原      settings.json 的 %s" % k)
            touched = True
        elif (c["had"] and k in cur and cur[k] == c["old"]) or (not c["had"] and k not in cur):
            msgs.append("  本来就是原样  settings.json 的 %s" % k)
        else:
            msgs.append("  保留      settings.json 的 %s（装完之后你改过它）" % k)
    if not touched:
        return ("none", None, msgs)
    if not existed and not cur:
        return ("delete", None, msgs + ["  删除      settings.json（里面空了，而且它本来就是安装时新建的）"])
    return ("write", settings_bytes(cur), msgs)


def undo(dest):
    for r in sweep_leftovers(dest):
        say("清理了上次被打断时留下的残渣：%s" % r)
    todo = [n for n in backups(dest) if not os.path.exists(os.path.join(dest, n, UNDONE))]
    if not todo:
        raise Stop("没有可以撤销的安装记录（%s 下没有未撤销的 %s*）。" % (dest, BACKUP_PREFIX))
    name = todo[-1]
    bdir = os.path.join(dest, name)
    m = json.load(open(os.path.join(bdir, MANIFEST), encoding="utf-8"))
    sact, sdata, smsgs = plan_settings_undo(m, dest, bdir)             # 先算（只读）：有问题就在动手前停下
    say("撤销安装记录 %s（%s）" % (m["time"], bdir))
    left = []
    for e in m["added"]:
        p = os.path.join(dest, e["rel"])
        if not os.path.exists(p):
            say("  已经不在  %s" % e["rel"])
        elif sha_file(p) != e["sha"]:
            say("  保留      %s（装完之后你改过它，没有删）" % e["rel"])
            left.append(e["rel"])
        else:
            os.unlink(p)
            say("  删除      %s" % e["rel"])
    for e in m["replaced"]:
        p, b = os.path.join(dest, e["rel"]), os.path.join(bdir, e["rel"])
        if not os.path.isfile(b):
            say("  无法还原  %s（备份里找不到）" % e["rel"])
            left.append(e["rel"])
        elif os.path.isfile(p) and sha_file(p) == sha_file(b):
            say("  本来就是原样  %s" % e["rel"])
        elif os.path.exists(p) and sha_file(p) != e["sha"]:
            say("  保留      %s（装完之后你改过它，没有还原；原来的在 %s）" % (e["rel"], b))
            left.append(e["rel"])
        else:
            restore_file(b, p)
            say("  还原      %s" % e["rel"])
    sp = os.path.join(dest, SETTINGS)
    if sact == "restore":
        restore_file(sdata, sp)
    elif sact == "delete":
        os.unlink(sp)
    elif sact == "write":
        write_atomic(sp, sdata, like=sp)
    for line in smsgs:
        say(line)
    for rel in sorted(m.get("created_dirs", []), key=lambda r: -r.count(os.sep)):
        d = os.path.join(dest, rel)
        try:
            os.rmdir(d)
            say("  删除空文件夹  %s" % rel)
        except OSError:
            pass                                                       # 不存在，或里面有别的东西：不动
    with open(os.path.join(bdir, UNDONE), "w") as f:
        f.write(time.strftime("%Y%m%d-%H%M%S") + "\n")
    say("")
    say("撤销完成；备份文件夹留着不删：%s" % bdir)
    if len(todo) > 1:
        say("还有 %d 次更早的安装记录，再运行一次 --undo 会继续撤销前一次。" % (len(todo) - 1))
    if left:
        say("没有动的文件：" + "、".join(left))
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="安装 pi 的 v1 配置包（先 --dry-run）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="只显示会发生什么，不改任何东西")
    g.add_argument("--status", action="store_true", help="显示现在装了什么")
    g.add_argument("--undo", action="store_true", help="撤销最近一次安装")
    ap.add_argument("--dest", help="pi 配置目录（默认 $PI_CODING_AGENT_DIR 或 ~/.pi/agent）")
    ap.add_argument("--pack", help="v1 包文件夹（默认本脚本旁边的 v1）")
    ap.add_argument("--create", action="store_true", help="目标目录不存在时也创建（全新机器才需要）")
    ap.add_argument("--allow-modified-pack", action="store_true", help="允许安装被改过的包")
    args = ap.parse_args(argv)
    try:
        dest = agent_dir(args)
        if args.undo:
            return undo(dest)
        pack = pack_dir(args)
        bad = check_pack(pack, args.allow_modified_pack)
        if args.status:
            return status(pack, dest)
        if bad:
            say("注意：这个包被改过（%s），继续是因为你加了 --allow-modified-pack。" % "；".join(bad))
        return install(pack, dest, args)
    except Stop as e:
        print("停下：%s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
