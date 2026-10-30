"""Prepare and run grouped Glass Expert jobs without changing the supplied macro.

prepare is offline. Only the explicit run command starts a CODE V COM session.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import dataclass
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import re
import sys
import xml.etree.ElementTree as ET


TOKEN = re.compile(r"[A-Za-z0-9_.+\-]+\Z")
COMMAND_ERROR = re.compile(r"(?im)^\s*(?:\*+\s*)?(?:error\s*:|sequence aborted\b|cannot open\b)")
IRRECOVERABLE_ERF = 9_999_000_000.001


class CandidateRejected(ValueError):
    """A proposed optical state failed a required material or evaluation check."""


class CodeVError(RuntimeError):
    """A command returned an explicit CODE V error diagnostic."""


@dataclass(frozen=True)
class Glass:
    catalog: str
    name: str

    @property
    def key(self):
        return ("PRIVATE" if self.catalog.upper() == "PRV" else self.catalog.upper(), self.name.upper())

    @property
    def code(self):
        return f"'{self.name}'" if self.key[0] == "PRIVATE" else f"{self.name}_{self.catalog}"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_candidates(path):
    """Validate the external TAB-delimited candidate list once at input."""
    rows = [line for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("!")]
    if not rows or [cell.strip().upper() for cell in rows[0].split("\t")[:2]] != ["CATALOG", "GLASS"]:
        raise ValueError(f"{path}: 首行必须是以TAB分隔的 Catalog / Glass")
    glasses = []
    seen = set()
    for number, row in enumerate(rows[1:], 2):
        cells = row.split("\t")
        if len(cells) < 2 or not all(TOKEN.fullmatch(cell.strip()) for cell in cells[:2]):
            raise ValueError(f"{path}:{number}: 库名和牌号必须是可用于CODE V的ASCII标记")
        glass = Glass(*(cell.strip() for cell in cells[:2]))
        if glass.key not in seen:
            glasses.append(glass)
            seen.add(glass.key)
    if not glasses:
        raise ValueError(f"{path}: 候选清单为空")
    return tuple(glasses)


INPUT_DEFAULTS = {
    "Surfaces": "", "Reset Vignetting": "NO", "Catalogs Used": "USER", "User Catalog": "",
    "SCHOTT Availability": "", "OHARA Availability": "", "HOYA Availability": "",
    "HIKARI Availability": "", "NHG Availability": "", "CDGM Availability": "",
    "Delete Special Glasses": "NO", "Eliminate Discontinued": "NO",
    "Include Possible Inaccurate Dispersion": "YES", "Max Strip Price Factor": "",
    "Exclude Glasses Without Prices": "NO", "Max Index": "", "Min Index": "",
    "Max Stain Resistance": "5", "Keep Unknown Stain": "YES", "Continue No Stain": "YES",
    "User Catalog After Filtering": "", "Absorption Factor": "0", "CTE Mismatch": "0",
    "Max Element Cost": "0", "Max Total Cost": "0", "Relative Weight": "0",
}


def prepare(config_file, output):
    """Read source files and installed catalog names; emit a portable offline job."""
    config_file = Path(config_file).resolve()
    config = json.loads(config_file.read_text(encoding="utf-8-sig"))
    unknown = set(config) - {"codev_root", "lens", "aut", "performance", "setup", "groups",
                             "passes", "neighbors", "relative_improvement", "joint_pairs",
                             "joint_trials", "seed", "native_settings"}
    if unknown:
        raise ValueError(f"未知配置项：{sorted(unknown)}")
    base = config_file.parent
    codev_root = (base / config["codev_root"]).resolve()
    native = codev_root / "macro" / "GlassExpert.seq"
    if not native.is_file() or not str(native).isascii() or '"' in str(native):
        raise ValueError("codev_root必须包含可从英文路径调用的macro/GlassExpert.seq")
    source_files = {"source.len": (base / config["lens"]).resolve(),
                    "aut.seq": (base / config["aut"]).resolve()}
    if source_files["source.len"].suffix.lower() != ".len":
        raise ValueError("lens请指定已保存的.len文件；SEQ模型应由用户先在CODE V中另存")
    if config.get("performance"):
        source_files["performance.seq"] = (base / config["performance"]).resolve()
    for i, file in enumerate(config.get("setup", []), 1):
        source_files[f"setup_{i:02d}.seq"] = (base / file).resolve()
    payloads = {f"assets/{name}": path.read_bytes() for name, path in source_files.items()}
    groups = []
    used_surfaces = set()
    source_lists = []
    for i, group in enumerate(config["groups"], 1):
        surfaces = group["surfaces"]
        if (not surfaces or any(type(s) is not int or s < 1 for s in surfaces)
                or len(set(surfaces)) != len(surfaces) or set(surfaces) & used_surfaces):
            raise ValueError("各组必须填写互不重叠的正整数材料面号；镜片序号不等于表面序号")
        if not isinstance(group["name"], str) or not group["name"].strip():
            raise ValueError("每组需要可辨认的name")
        used_surfaces.update(surfaces)
        source_list = (base / group["candidates"]).resolve()
        candidates = read_candidates(source_list)
        source_lists.append(source_list)
        candidate_file = f"assets/group_{i:02d}.dat"
        payloads[candidate_file] = ("Catalog\tGlass\r\n" + "".join(
            f"{glass.catalog}\t{glass.name}\r\n" for glass in candidates)).encode("ascii")
        groups.append({"name": group["name"], "surfaces": surfaces,
                       "candidates": [[g.catalog, g.name] for g in candidates],
                       "candidate_file": candidate_file, "inputs_file": f"assets/group_{i:02d}.inputs.dat"})
    if not groups:
        raise ValueError("至少需要一组镜片及候选清单")

    wanted = {Glass(*item).key for group in groups for item in group["candidates"]}
    found = set()
    catalog_sources = []
    for file in sorted((codev_root / "glass").glob("*.xml")):
        root = ET.parse(file).getroot()
        catalog = (root.findtext("Name") or "").upper()
        if catalog not in {item[0] for item in wanted}:
            continue
        present = {(catalog, (glass.findtext("GlassName") or "").upper())
                   for glass in root.findall("./Glasses/Glass")}
        found.update(present & wanted)
        catalog_sources.append({"path": str(file), "sha256": digest(file.read_bytes())})
    if wanted - found:
        raise ValueError("已安装XML中未找到候选材料：" + ", ".join(
            f"{catalog}/{name}" for catalog, name in sorted(wanted - found))
            + "。本版候选清单使用已安装库；镜头内PRV需另行提供可核对的材料来源。")

    settings = INPUT_DEFAULTS | config.get("native_settings", {})
    if set(settings) - set(INPUT_DEFAULTS):
        raise ValueError("native_settings包含未记录的原生输入关键字")
    if float(settings["Relative Weight"] or 0) != 0:
        raise ValueError("多轮搜索的Relative Weight会按每次原生运行重置基准；本版请设0，并在AUT中约束绝对重量")
    for i, group in enumerate(groups, 1):
        values = settings | {"Surfaces": " ".join(map(str, group["surfaces"])),
                             "Catalogs Used": "USER", "User Catalog": group["candidate_file"],
                             "User Catalog After Filtering": ""}
        payloads[group["inputs_file"]] = "".join(
            f"{key}:\t{value}\r\n" for key, value in values.items()).encode("ascii")

    options = {"passes": config.get("passes", 3), "neighbors": config.get("neighbors", 7),
               "relative_improvement": config.get("relative_improvement", 1e-4),
               "joint_trials": config.get("joint_trials", 0), "seed": config.get("seed", 1)}
    for name in ("passes", "neighbors", "joint_trials", "seed"):
        if type(options[name]) is not int or options[name] < (1 if name in {"passes", "neighbors"} else 0):
            raise ValueError(f"{name}必须是范围内的整数")
    if not isinstance(options["relative_improvement"], (int, float)) or not 0 <= options["relative_improvement"] < 1:
        raise ValueError("relative_improvement须在[0,1)范围内")
    if options["joint_trials"] and any(str(settings[key]) != str(value) for key, value in INPUT_DEFAULTS.items()):
        raise ValueError("联合换料只应用DAT白名单和AUT约束；请把附加原生筛选移到候选DAT/AUT，或将joint_trials设0")
    surface_group = {surface: i for i, group in enumerate(groups) for surface in group["surfaces"]}
    pairs = config.get("joint_pairs", [])
    if options["joint_trials"] and not pairs:
        pairs = [list(pair) for pair in itertools.combinations(sorted(used_surfaces), 2)
                 if surface_group[pair[0]] != surface_group[pair[1]]]
    seen_pairs = set()
    for pair in pairs:
        if (len(pair) != 2 or any(type(s) is not int or s not in surface_group for s in pair)
                or surface_group[pair[0]] == surface_group[pair[1]]):
            raise ValueError("joint_pairs每项须为两个不同组中已配置的面号")
        key = tuple(sorted(pair))
        if key in seen_pairs:
            raise ValueError("joint_pairs存在重复面号对")
        seen_pairs.add(key)
    options["joint_pairs"] = pairs

    plan = {"schema": 1, "status": "prepared_offline_not_optically_verified",
            "native_macro": str(native), "native_sha256": digest(native.read_bytes()),
            "codev_root": str(codev_root), "groups": groups, "options": options,
            "performance": "assets/performance.seq" if config.get("performance") else None,
            "setup": [f"assets/{name}" for name in source_files if name.startswith("setup_")],
            "assets": {name: digest(data) for name, data in payloads.items()},
            "sources": [{"path": str(path), "sha256": digest(payloads[f"assets/{name}"])}
                        for name, path in source_files.items()],
            "candidate_sources": [str(path) for path in source_lists], "catalog_sources": catalog_sources}
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "assets").mkdir()
    for name, data in payloads.items():
        (output / name).write_bytes(data)
    (output / "job.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def cv_string(value):
    text = str(value).strip()
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"" else text


class Session:
    """The one boundary for COM output, commands and optical state reads."""

    def __init__(self, application, log):
        self.application = application
        self.log = log

    def command(self, command):
        result = str(self.application.Command(command) or "")
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(f"\n> {command}\n{result}\n")
        if COMMAND_ERROR.search(result):
            raise CodeVError(result.strip())
        return result

    def number(self, expression):
        value = float(cv_string(self.application.EvaluateExpression(expression)).replace("D", "E"))
        if not math.isfinite(value):
            raise CandidateRejected(f"{expression}返回非有限值")
        return value

    def score(self):
        if self.number("(OPT ERF)") != 1:
            raise CandidateRejected("AUT评价不存在或已过期；不使用旧评价排序")
        value = self.number("(AUT.ERF ALL)")
        if value < 0 or value == IRRECOVERABLE_ERF:
            raise CandidateRejected("AUT未得到可用评价")
        return value

    def materials(self):
        surfaces, zooms = int(self.number("(NUM S)")), int(self.number("(NUM Z)"))
        result = {}
        for zoom in range(1, zooms + 1):
            for surface in range(surfaces + 1):
                name = cv_string(self.application.EvaluateExpression(f"(GLA S{surface} Z{zoom})"))
                catalog = cv_string(self.application.EvaluateExpression(f"(GLA S{surface} Z{zoom} CAT)"))
                result[(surface, zoom)] = Glass(catalog, name).key
        return result


def check_materials(state, groups, before=None, mutable=()):
    """Check actual CODE V materials, including unselected surfaces and every zoom."""
    for group in groups:
        allowed = {Glass(*item).key for item in group["candidates"]}
        for surface in group["surfaces"]:
            keys = [key for key in state if key[0] == surface]
            if not keys or any(state[key] not in allowed for key in keys):
                raise CandidateRejected(f"{group['name']}的S{surface}不在本组候选清单中")
            if len({state[key] for key in keys}) != 1:
                raise CandidateRejected(f"S{surface}材料随组态变化；本版要求各组态使用同一材料")
    if before is not None and (set(state) != set(before) or any(
            state[key] != before[key] for key in before if key[0] not in mutable)):
        raise CandidateRejected("未参与本轮换料的材料或表面/组态结构发生变化")


def capture_sequence(state, groups, mutable, snapshot, performance=None):
    """A separate performance callback snapshots only legal native improvements."""
    lines = ["! Generated grouped-glass callback; the supplied GlassExpert.seq is unchanged.",
             "GBL NUM ^GGX_BEST ^GGX_SCORE ^GGX_OK ^GGX_MATCH ^GGX_REJECTS",
             "^GGX_OK == 1"]
    allowed_by_surface = {s: [Glass(*item).key for item in group["candidates"]]
                          for group in groups for s in group["surfaces"]}
    for (surface, zoom), material in sorted(state.items()):
        allowed = allowed_by_surface[surface] if surface in mutable else [material]
        lines.append("^GGX_MATCH == 0")
        for catalog, name in allowed:
            lines += [f'IF UPCASE((GLA S{surface} Z{zoom})) = "{name}" AND &',
                      f'   UPCASE((GLA S{surface} Z{zoom} CAT)) = "{catalog}"',
                      "    ^GGX_MATCH == 1", "END IF"]
        lines += ["IF ^GGX_MATCH = 0", "    ^GGX_OK == 0", "END IF"]
        if surface in mutable and zoom > 1:
            lines += [f"IF (GLA S{surface} Z{zoom}) <> (GLA S{surface} Z1) OR &",
                      f"   (GLA S{surface} Z{zoom} CAT) <> (GLA S{surface} Z1 CAT)",
                      "    ^GGX_OK == 0", "END IF"]
    lines += ["IF (OPT ERF) <> 1", "    ^GGX_OK == 0", "END IF", "IF ^GGX_OK = 1",
              "    ^GGX_SCORE == (AUT.ERF ALL)",
              f"    IF ^GGX_SCORE < 0 OR ^GGX_SCORE = {IRRECOVERABLE_ERF}",
              "        ^GGX_OK == 0", "    END IF", "END IF", "IF ^GGX_OK = 1",
              "    IF ^GGX_SCORE < ^GGX_BEST", f'        SAV "{snapshot}"',
              "        ^GGX_BEST == ^GGX_SCORE", "    END IF", "ELS",
              "    ^GGX_REJECTS == ^GGX_REJECTS + 1", "END IF"]
    if performance:
        lines.append(f'IN "{performance}"')
    return "\r\n".join(lines) + "\r\n"


def joint_candidates(groups, state, pairs, budget, rng):
    """Sample legal cross-group substitutions without building the full product."""
    allowed = {s: [Glass(*item) for item in group["candidates"]]
               for group in groups for s in group["surfaces"]}
    blocks, ends, total = [], [], 0
    for left, right in pairs:
        a = [g for g in allowed[left] if g.key != state[(left, 1)]]
        b = [g for g in allowed[right] if g.key != state[(right, 1)]]
        size = len(a) * len(b)
        if size:
            blocks.append((left, right, a, b, total))
            total += size
            ends.append(total)
    indices = list(range(total)) if total <= budget else rng.sample(range(total), budget)
    for index in indices:
        left, right, a, b, start = blocks[bisect_right(ends, index)]
        i, j = divmod(index - start, len(b))
        yield {left: a[i], right: b[j]}


def run_job(job_file, application):
    """Execute against an explicit application; tests supply an offline simulator."""
    job_file = Path(job_file).resolve()
    root = job_file.parent
    if not str(root).isascii() or '"' in str(root):
        raise ValueError("运行目录须为英文路径；可将准备好的整个目录复制后运行")
    plan = json.loads(job_file.read_text(encoding="utf-8"))
    native = Path(plan["native_macro"])
    if digest(native.read_bytes()) != plan["native_sha256"]:
        raise ValueError("原生宏与准备时版本不一致，请重新prepare")
    for file, expected in plan["assets"].items():
        if digest((root / file).read_bytes()) != expected:
            raise ValueError(f"准备文件已改变：{file}；请重新prepare")
    for source in plan["catalog_sources"]:
        if digest(Path(source["path"]).read_bytes()) != source["sha256"]:
            raise ValueError("候选材料库在准备后改变，请重新prepare")
    results = root / "results"
    results.mkdir(exist_ok=False)
    session = Session(application, results / "commands.log")
    groups, options = plan["groups"], plan["options"]
    report = {"status": "running", "native_sha256": plan["native_sha256"], "events": [],
              "evaluation": "AUT.ERF ALL; same supplied AUT sequence", "optical_validation": "user_run"}

    def record(event):
        report["events"].append(event)
        (results / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    source_snapshot = results / "source_before.len"
    accepted = results / "accepted_000.len"
    aut = root / "assets/aut.seq"
    rng = random.Random(options["seed"])
    try:
        session.command(f'RES "{root / "assets/source.len"}"')
        session.command(f'SAV "{source_snapshot}"')
        for setup in plan["setup"]:
            session.command(f'IN "{root / setup}"')
        source_state = session.materials()
        check_materials(source_state, groups)
        session.command(f'IN "{aut}"')
        state = session.materials()
        check_materials(state, groups, source_state, ())
        score = session.score()
        session.command(f'SAV "{accepted}"')
        record({"type": "baseline", "score": score, "file": str(accepted)})
        for pass_number in range(1, options["passes"] + 1):
            pass_start = score
            for i, group in enumerate(groups, 1):
                session.command(f'RES "{accepted}"')
                before = session.materials()
                checkpoint = results / f"native_p{pass_number:02d}_g{i:02d}.len"
                session.command(f'SAV "{checkpoint}"')
                callback = results / f"capture_p{pass_number:02d}_g{i:02d}.seq"
                performance = str(root / plan["performance"]) if plan["performance"] else None
                callback.write_text(capture_sequence(before, groups, group["surfaces"], str(checkpoint), performance),
                                    encoding="ascii", newline="")
                session.command("GBL NUM ^GGX_BEST ^GGX_REJECTS")
                session.command(f"^GGX_BEST == {score:.17g}")
                session.command("^GGX_REJECTS == 0")
                inputs = root / group["inputs_file"]
                session.command(f'IN "{native}" "{aut}" "{callback}" "{inputs}" {options["neighbors"]} N')
                rejected = session.number("^GGX_REJECTS")
                session.command(f'RES "{checkpoint}"')
                state = session.materials()
                check_materials(state, groups, before, group["surfaces"])
                new_score = session.score()
                if new_score > score:
                    raise CandidateRejected("原生回调快照比原有合法解差；停止并保留先前快照")
                score, accepted = new_score, checkpoint
                record({"type": "native_group", "pass": pass_number, "group": group["name"],
                        "score": score, "rejected_captures": int(rejected), "file": str(accepted)})

            if options["joint_trials"]:
                parent, parent_score = accepted, score
                session.command(f'RES "{parent}"')
                parent_state = session.materials()
                moves = list(joint_candidates(groups, parent_state, options["joint_pairs"], options["joint_trials"], rng))
                for trial, move in enumerate(moves, 1):
                    session.command(f'RES "{parent}"')
                    event = {"type": "joint", "pass": pass_number, "trial": trial,
                             "changes": {str(s): [g.catalog, g.name] for s, g in move.items()}}
                    try:
                        for surface, glass in move.items():
                            session.command(f"GLA S{surface} {glass.code}")
                        session.command(f'IN "{aut}"')
                        candidate_state = session.materials()
                        check_materials(candidate_state, groups, parent_state, move)
                        candidate_score = session.score()
                        event.update(score=candidate_score, accepted=candidate_score < score)
                        if candidate_score < score:
                            accepted = results / f"joint_p{pass_number:02d}_t{trial:04d}.len"
                            session.command(f'SAV "{accepted}"')
                            score = candidate_score
                            event["file"] = str(accepted)
                    except (CandidateRejected, CodeVError) as error:
                        event.update(accepted=False, rejection=str(error))
                    record(event)
                record({"type": "joint_batch", "pass": pass_number, "parent_score": parent_score,
                        "score": score, "trials": len(moves), "file": str(accepted)})
                session.command(f'RES "{accepted}"')
            improvement = (pass_start - score) / max(abs(pass_start), 1e-30)
            record({"type": "pass", "pass": pass_number, "score": score, "relative_improvement": improvement})
            if improvement <= options["relative_improvement"]:
                break
        session.command(f'RES "{accepted}"')
        check_materials(session.materials(), groups)
        final = results / "final.len"
        session.command(f'SAV "{final}"')
        report.update(status="completed", final_score=session.score(), final_file=str(final),
                      remaining="Verify optical specifications, manufacturing constraints and tolerancing separately")
        record({"type": "final", "score": report["final_score"], "file": str(final)})
        return report
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="failed", error=str(error), last_accepted=str(accepted), source_snapshot=str(source_snapshot))
        record({"type": "failure", "error": str(error)})
        raise


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare", help="离线核对材料并生成任务，不启动CODE V")
    prepare_parser.add_argument("config")
    prepare_parser.add_argument("--out", required=True)
    run_parser = sub.add_parser("run", help="明确启动独立CODE V COM会话并执行准备好的任务")
    run_parser.add_argument("job")
    args = parser.parse_args()
    if args.command == "prepare":
        plan = prepare(args.config, args.out)
        print(json.dumps({"status": plan["status"], "job": str(Path(args.out).resolve() / "job.json"),
                          "groups": [{"name": g["name"], "surfaces": g["surfaces"],
                                      "candidates": len(g["candidates"])} for g in plan["groups"]]}, ensure_ascii=False, indent=2))
        return
    # No COM import, activation or software launch occurs in prepare or module import.
    import win32com.client
    job = Path(args.job).resolve()
    if not str(job.parent).isascii():
        raise ValueError("请将完整任务目录复制到英文路径，再运行")
    application = win32com.client.DispatchEx("CODEV.Application")
    application.StartingDirectory = str(job.parent)
    try:
        application.StartCodeV()
        result = run_job(job, application)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        application.StopCodeV()


if __name__ == "__main__":
    main()
