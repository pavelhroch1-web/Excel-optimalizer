"""
Verifies that SYNC-BLOCK-marked sections in deployable office-scripts/*.ts
files are byte-identical (module-level `export ` keywords aside) to the
canonical blocks in office-scripts/shared/*.ts.

Office Scripts cannot import across files, so shared logic is duplicated by
hand into each deployable script. That duplication has already caused two
real bugs in this project (a stale diacritics regex, a missing norm() call
in an address-dedup key) - this script exists so drift is caught by a
one-command check instead of being found by accident.

Usage: python3 tools/check_sync.py
Exit code 0 = all blocks match. Non-zero = at least one mismatch (printed).
"""
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED_DIR = ROOT / "office-scripts" / "shared"
DEPLOYABLE_FILES = [
    ROOT / "office-scripts" / "ImportEngine.ts",
    ROOT / "office-scripts" / "PlanningEngine.ts",
    ROOT / "office-scripts" / "ComplianceEngine.ts",
    ROOT / "office-scripts" / "AdvisorEngine.ts",
    ROOT / "office-scripts" / "ReportingEngine.ts",
    ROOT / "office-scripts" / "PublishEngine.ts",
    ROOT / "office-scripts" / "PerformanceEngine.ts",
]

BLOCK_RE = re.compile(
    r"// SYNC-BLOCK-START: (?P<name>[^\n]+)\n(?P<body>.*?)\n[ \t]*// SYNC-BLOCK-END: (?P=name)",
    re.DOTALL,
)


def extract_blocks(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    blocks = {}
    for m in BLOCK_RE.finditer(text):
        name = m.group("name").strip()
        blocks.setdefault(name, []).append(m.group("body"))
    return blocks


def normalize(body: str) -> str:
    # Allowed textual differences between shared/*.ts (top-level, `export
    # function ...`) and deployable scripts (nested one level inside
    # `main() {`, plain `function ...`): the `export ` keyword, and a
    # constant extra indentation level from that nesting. Both are stripped
    # here so the underlying logic is compared, not incidental formatting.
    lines = [re.sub(r"^(\s*)export ", r"\1", line) for line in body.split("\n")]
    dedented = textwrap.dedent("\n".join(lines))
    return "\n".join(line.rstrip() for line in dedented.split("\n")).strip()


def main() -> int:
    shared_blocks = {}
    for shared_file in sorted(SHARED_DIR.glob("*.ts")):
        blocks = extract_blocks(shared_file)
        for name, bodies in blocks.items():
            if len(bodies) != 1:
                print(f"ERROR: {shared_file.name} defines block '{name}' {len(bodies)} times, expected 1")
                return 1
            shared_blocks[name] = (shared_file.name, normalize(bodies[0]))

    # core.ts blocks are named per-consumer (e.g. "core.ts (planning)") since
    # different engines inline different subsets of core.ts's functions -
    # there is no single canonical "core.ts" block to diff a whole-file copy
    # against. Those are checked separately: every function body found inside
    # a "core.ts (...)" deployable block must appear verbatim in core.ts.
    core_ts_path = SHARED_DIR / "core.ts"
    core_ts_text = normalize(core_ts_path.read_text(encoding="utf-8"))

    failures = []
    checked = 0

    for deployable in DEPLOYABLE_FILES:
        if not deployable.exists():
            continue
        blocks = extract_blocks(deployable)
        for name, bodies in blocks.items():
            for body in bodies:
                checked += 1
                normalized = normalize(body)
                if name.startswith("core.ts"):
                    # Function-by-function containment check against core.ts,
                    # since each engine only inlines a subset.
                    functions = re.findall(
                        r"(?:function \w+\([^)]*\)[^{]*\{.*?\n\}|interface \w+ \{.*?\n\})",
                        normalized,
                        re.DOTALL,
                    )
                    if not functions:
                        failures.append(f"{deployable.name}: block '{name}' - no functions/interfaces found to check")
                        continue
                    for fn in functions:
                        if fn.strip() not in core_ts_text:
                            failures.append(
                                f"{deployable.name}: block '{name}' contains a definition not found "
                                f"verbatim in core.ts (first line: {fn.splitlines()[0].strip()!r})"
                            )
                    continue

                if name not in shared_blocks:
                    failures.append(f"{deployable.name}: block '{name}' has no matching shared/*.ts source")
                    continue
                source_file, canonical = shared_blocks[name]
                if normalized != canonical:
                    failures.append(
                        f"{deployable.name}: block '{name}' does not match shared/{source_file} - drift detected"
                    )

    if failures:
        print(f"SYNC CHECK FAILED ({len(failures)} issue(s), {checked} block(s) checked):")
        for f in failures:
            print("  - " + f)
        return 1

    print(f"Sync check passed: {checked} block(s) verified across {len(DEPLOYABLE_FILES)} deployable script(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
