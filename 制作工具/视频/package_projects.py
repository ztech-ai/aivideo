"""将已完成的短视频工程按项目目录封装为 ZIP，保留成片、剪辑源和原文。"""

import argparse
import tempfile
import zipfile
from pathlib import Path


DOCUMENTS = (
    "index.html", "hyperframes.json", "package.json", "script.json",
    "captions.json", "创作提炼.md", "短视频脚本.md", "制作说明.md",
    "字幕.srt", "SPEC.md", "REVIEW.md", "成片.mp4",
)
TREES = ("assets", "compositions", "原文")
ALREADY_COMPRESSED = {".docx", ".jpg", ".jpeg", ".png", ".mp3", ".mp4", ".woff", ".woff2"}


def entries_for(project):
    project = project.resolve(strict=True)
    files = [project / name for name in DOCUMENTS]
    for name in TREES:
        directory = project / name
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"缺少工程目录或目录为符号链接：{directory}")
        children = [p for p in directory.rglob("*") if not any(
            part.startswith(".") for part in p.relative_to(directory).parts)]
        if any(p.is_symlink() for p in children):
            raise ValueError(f"目录内含符号链接，请先将对应素材放入工程：{directory}")
        members = [p for p in children if p.is_file()]
        if not members:
            raise ValueError(f"工程目录为空：{directory}")
        files.extend(members)
    for source in sorted(files):
        if source.is_symlink() or not source.is_file() or not source.stat().st_size:
            raise ValueError(f"工程文件缺失、为空或为符号链接：{source}")
        if source.stat().st_size < 1024:
            with source.open("rb") as f:
                if f.readline(70).rstrip() == b"version https://git-lfs.github.com/spec/v1":
                    raise ValueError(f"缺少 Git LFS 实体，请先执行 git lfs pull：{source}")
        yield source, (Path(project.name) / source.relative_to(project)).as_posix()


def build_archive(entries, output, replace):
    if output.suffix.lower() != ".zip":
        raise ValueError("输出文件名需要以 .zip 结尾")
    if output.exists() and not replace:
        raise ValueError(f"输出文件已存在。若需覆盖，请使用 --replace：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".video-package-", dir=output.parent) as staging:
        pending = Path(staging) / "project.zip"
        with zipfile.ZipFile(pending, "w", allowZip64=True) as archive:
            for source, member in entries:
                method = zipfile.ZIP_STORED if source.suffix.lower() in ALREADY_COMPRESSED else zipfile.ZIP_DEFLATED
                archive.write(source, member, compress_type=method,
                              compresslevel=4 if method == zipfile.ZIP_DEFLATED else None)
        with zipfile.ZipFile(pending) as archive:
            if archive.namelist() != [member for _, member in entries]:
                raise ValueError("ZIP 内文件清单与源工程不符")
            corrupt = archive.testzip()
            if corrupt:
                raise ValueError(f"ZIP CRC 校验失败：{corrupt}")
        if replace:
            pending.replace(output)
        else:
            # 同文件系统内发布，若其他进程先创建了同名 ZIP 则保留它。
            output.hardlink_to(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", type=Path, nargs="+", metavar="PROJECT")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-o", "--output", type=Path, metavar="ZIP", help="指定 ZIP 文件路径")
    mode.add_argument("--list", action="store_true", help="检查并打印清单，不写入文件")
    parser.add_argument("--replace", action="store_true", help="校验通过后替换已有 ZIP")
    args = parser.parse_args()
    if args.replace and not args.output:
        parser.error("--replace 需要与 --output 一起使用")
    try:
        projects = [p.resolve(strict=True) for p in args.projects]
        if len({p.name for p in projects}) != len(projects):
            raise ValueError("待归档的项目请使用不同的目录名")
        if args.output:
            output = args.output.absolute()
            if any(output.resolve().is_relative_to(p) for p in projects):
                raise ValueError("请将输出 ZIP 放在项目目录之外")
        entries = sorted((entry for p in projects for entry in entries_for(p)), key=lambda e: e[1])
        if args.list:
            for _, member in entries:
                print(member)
        else:
            build_archive(entries, output, args.replace)
            print(f"已打包 {len(projects)} 个项目、{len(entries)} 个文件：{output}")
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        parser.exit(1, f"错误：{e}\n")


if __name__ == "__main__":
    main()
