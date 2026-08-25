# -*- coding: utf-8 -*-
"""免米AI学堂 内容仓发布脚本。

用法(在任意目录执行):
    python -X utf8 tools/publish.py          # 同步 md+图片、重建 README 目录、核验引用(不碰 git)
    python -X utf8 tools/publish.py --push   # 同步+核验通过后 git add/commit/push

数据流(源 → 仓库):
    v2正文草稿\\Codex教程-第N篇正文-草稿.md      → content/Codex零基础入门教程/第NN篇 标题.md
    v2正文草稿\\图片和附件水印版\\NN\\*          → content/Codex零基础入门教程/img/NN/*(去「-水印版」后缀)
    通用语法手册\\Markdown教程-第i篇*-正文-定稿.md → content/Markdown通用语法手册/第i篇 标题.md
    通用语法手册\\图片和附件水印版\\*             → content/Markdown通用语法手册/img/*(去后缀)

改写规则(代码围栏内的教学示例一律不动):
    ](图片和附件/...   → ](img/...
    src="图片和附件/... → src="img/...
    Codex 教程的 ](盘符绝对路径\\xxx.png) → ](img/NN/xxx.png)
    语法手册的 ](盘符绝对路径\\xxx.png)     → ](img/xxx.png)

README 目录只重建 AUTO-TOC 标记之间的部分,标记外的介绍文字可随意手工编辑。
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote

REPO = Path(r"D:\Develop\mianmi-ai-tutorials")
CONTENT = REPO / "content"

V2 = Path(r"D:\Users\Desktop\启舰\v2正文草稿")
MDBOOK = Path(r"D:\Users\Desktop\启舰\markdown教程\通用语法手册")

WM_SUFFIX = "-水印版"
TOC_START = "<!-- AUTO-TOC:START 此区块由 tools/publish.py 自动生成,勿手工编辑 -->"
TOC_END = "<!-- AUTO-TOC:END -->"

MANUAL_PARTS = ["入门", "速查表", "基本语法", "扩展语法", "变通", "工具"]

TUTORIALS = [
    {
        "name": "Codex零基础入门教程",
        "episodes": [
            {
                "src": V2 / ("Codex教程-第%d篇正文-草稿.md" % n),
                "label": "第%02d篇" % n,
                "img_subdir": "%02d" % n,
            }
            for n in range(1, 21)
        ],
        "img_src": V2 / "图片和附件水印版",
        "img_nested": True,
    },
    {
        "name": "Markdown通用语法手册",
        "episodes": [
            {
                "src": MDBOOK / ("Markdown教程-第%d篇%s-正文-定稿.md" % (i, part)),
                "label": "第%d篇" % i,
            }
            for i, part in enumerate(MANUAL_PARTS, 1)
        ],
        "img_src": MDBOOK / "图片和附件水印版",
        "img_nested": False,
    },
]

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ABS_REF_RE = re.compile(r"\]\([A-Za-z]:\\[^)]*\\([^\\)]+)\)")
IMG_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
IMG_HTML_RE = re.compile(r'<img[^>]+src="([^"]+)"')


def iter_lines(text):
    """逐行产出 (行号, 行文本, 是否在代码围栏内)。围栏按 CommonMark 口径配对。"""
    open_fence = None  # (字符, 长度)
    for i, ln in enumerate(text.splitlines(), 1):
        m = FENCE_RE.match(ln)
        if m:
            marker = m.group(1)
            ch, length = marker[0], len(marker)
            if open_fence is None:
                open_fence = (ch, length)
                yield i, ln, True
                continue
            if ch == open_fence[0] and length >= open_fence[1] and ln.strip() == marker:
                open_fence = None
                yield i, ln, True
                continue
            yield i, ln, True
            continue
        yield i, ln, open_fence is not None


def rewrite_line(line, img_subdir=None):
    line = line.replace("](图片和附件/", "](img/")
    line = line.replace('src="图片和附件/', 'src="img/')
    img_prefix = "img/%s/" % img_subdir if img_subdir else "img/"
    line = ABS_REF_RE.sub(lambda m: "](" + img_prefix + m.group(1) + ")", line)
    return line


def article_filename(label, title):
    """生成 Windows、Git 和 Markdown 链接均可安全使用的文章文件名。"""
    safe_title = INVALID_FILENAME_RE.sub("-", title).rstrip(" .")
    return "%s %s.md" % (label, safe_title or label)


def sync_images(tut):
    dst_root = CONTENT / tut["name"] / "img"
    if dst_root.exists():
        shutil.rmtree(dst_root)
    count = 0
    src_root = tut["img_src"]
    if tut["img_nested"]:
        subdirs = sorted(p for p in src_root.iterdir() if p.is_dir())
        for sub in subdirs:
            for f in sorted(sub.iterdir()):
                if not f.is_file():
                    continue
                tgt = dst_root / sub.name / f.name.replace(WM_SUFFIX, "")
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, tgt)
                count += 1
    else:
        for f in sorted(src_root.iterdir()):
            if not f.is_file():
                continue
            tgt = dst_root / f.name.replace(WM_SUFFIX, "")
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, tgt)
            count += 1
    return count


def sync_md(tut):
    out_dir = CONTENT / tut["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    titles = []
    for ep in tut["episodes"]:
        text = ep["src"].read_text(encoding="utf-8")
        out = []
        title = None
        for _, ln, in_fence in iter_lines(text):
            if not in_fence:
                if title is None:
                    heading = HEADING_RE.match(ln)
                    if heading:
                        title = heading.group(1).strip()
                ln = rewrite_line(ln, ep.get("img_subdir"))
            out.append(ln)
        title = title or ep["label"]
        dst = article_filename(ep["label"], title)
        rendered.append((dst, "\n".join(out) + "\n"))
        titles.append((ep["label"], title, dst))

    destinations = [dst.casefold() for dst, _ in rendered]
    if len(destinations) != len(set(destinations)):
        raise ValueError("文章标题生成了重复文件名: %s" % tut["name"])

    # content 下的文章由本工具统一生成；写入新名称前先清除旧文档。
    for old in out_dir.glob("*.md"):
        old.unlink()
    for dst, text in rendered:
        (out_dir / dst).write_text(text, encoding="utf-8")
    return titles


def gen_toc(all_titles):
    lines = [TOC_START, "", "## 目录", ""]
    for name, titles in all_titles:
        lines.append("### %s" % name)
        lines.append("")
        for label, title, dst in titles:
            rel = "content/%s/%s" % (name, dst)
            lines.append("* [%s %s](%s)" % (label, title, quote(rel, safe="/")))
        lines.append("")
    lines.append("持续更新中……")
    lines.append("")
    lines.append(TOC_END)
    return "\n".join(lines)


def update_readme(toc):
    rd = REPO / "README.md"
    text = rd.read_text(encoding="utf-8") if rd.exists() else "# mianmi-ai-tutorials\n"
    if TOC_START in text and TOC_END in text:
        pre = text.split(TOC_START)[0]
        post = text.split(TOC_END, 1)[1]
        text = pre + toc + post
    else:
        if not text.endswith("\n"):
            text += "\n"
        text = text + "\n" + toc + "\n"
    rd.write_text(text, encoding="utf-8")


def verify():
    total = 0
    problems = []
    for tut in TUTORIALS:
        d = CONTENT / tut["name"]
        for md in sorted(d.glob("*.md")):
            text = md.read_text(encoding="utf-8")
            for i, ln, in_fence in iter_lines(text):
                if in_fence:
                    continue
                refs = IMG_MD_RE.findall(ln) + IMG_HTML_RE.findall(ln)
                for ref in refs:
                    if ref.startswith(("http://", "https://", "data:")):
                        continue
                    total += 1
                    target = unquote(ref)
                    if "图片和附件" in target:
                        problems.append("%s:%d 残留源路径: %s" % (md.name, i, ref))
                    elif not (md.parent / target).exists():
                        problems.append("%s:%d 缺文件: %s" % (md.name, i, ref))
    return total, problems


def git_push(message):
    for cmd in (
        ["git", "add", "-A"],
        ["git", "commit", "-m", message],
        ["git", "push"],
    ):
        r = subprocess.run(cmd, cwd=str(REPO))
        if r.returncode != 0:
            print("git 命令失败: %s" % " ".join(cmd))
            return False
    return True


def main():
    ap = argparse.ArgumentParser(description="免米AI学堂内容仓发布脚本")
    ap.add_argument("--push", action="store_true", help="同步并核验通过后 git add/commit/push")
    ap.add_argument("--msg", default="更新教程内容", help="--push 时的提交说明")
    args = ap.parse_args()

    CONTENT.mkdir(exist_ok=True)
    all_titles = []
    for tut in TUTORIALS:
        n_img = sync_images(tut)
        titles = sync_md(tut)
        all_titles.append((tut["name"], titles))
        print("[%s] md %d 篇, 图片 %d 张" % (tut["name"], len(titles), n_img))

    update_readme(gen_toc(all_titles))
    print("[README] 目录已重建")

    total, problems = verify()
    print("[核验] 图片引用共 %d 个" % total)
    if problems:
        for p in problems:
            print("  [问题] %s" % p)
        print("[核验] 共 %d 个问题,请先处理" % len(problems))
        sys.exit(1)
    print("[核验] 全部引用有效")

    if args.push:
        if git_push(args.msg):
            print("已推送到远程")
        else:
            sys.exit(1)
    else:
        print("完成(本地预览,未执行 git 操作)")


if __name__ == "__main__":
    main()
