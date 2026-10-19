import subprocess
from dataclasses import dataclass

GATES = (("test_310", "part_310"), ("test_312", "part_312"))

GATE_BLOCK_MUTATIONS = ("or_true", "set_plus_e", "if_false")

MACOS_PARTS = ("macos_312_1", "macos_312_2", "macos_312_3", "macos_312_4")

def gate_readers() -> tuple[str, ...]:
    try:
        import yaml
    except ImportError:
        return ("indent",)
    return ("yaml", "indent")

@dataclass(frozen=True)
class WorkflowSwitch:
    expression: str

    def exported(self, event_name: str) -> str:
        value = self.expression.strip()
        if value in ("'1'", '"1"', "1"):
            return "1"
        raise AssertionError("cannot read the real-process switch")

def read_workflow_switch(text: str, reader: str) -> WorkflowSwitch:
    if reader == "yaml":
        return yaml_workflow_switch(text)
    if reader == "indent":
        return indent_workflow_switch(text)
    raise AssertionError(reader)

def yaml_workflow_switch(text: str) -> WorkflowSwitch:
    try:
        import yaml
    except ImportError:
        raise AssertionError("cannot find the real-process switch") from None
    loaded = yaml.safe_load(text)
    env = loaded.get("env") if isinstance(loaded, dict) else None
    value = env.get("PSTACK_TEST_REAL_PROCESS") if isinstance(env, dict) else None
    if value != "1":
        raise AssertionError("cannot find the real-process switch")
    jobs = loaded.get("jobs") if isinstance(loaded, dict) else None
    if not isinstance(jobs, dict):
        raise AssertionError("cannot find the real-process switch")
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            raise AssertionError("cannot find the real-process switch")
        job_env = job.get("env")
        if isinstance(job_env, dict) and "PSTACK_TEST_REAL_PROCESS" in job_env:
            raise AssertionError("cannot find the real-process switch")
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise AssertionError("cannot find the real-process switch")
        for step in steps:
            step_env = step.get("env") if isinstance(step, dict) else None
            if isinstance(step_env, dict) and "PSTACK_TEST_REAL_PROCESS" in step_env:
                raise AssertionError("cannot find the real-process switch")
    return WorkflowSwitch(value)

def indent_workflow_switch(text: str) -> WorkflowSwitch:
    lines = text.splitlines(keepends=True)
    key = "PSTACK_TEST_REAL_PROCESS"
    if sum(key in line for line in lines) != 1:
        raise AssertionError("cannot find the real-process switch")
    header = next((index for index, line in enumerate(lines) if line.rstrip("\n") == "env:"), None)
    if header is None:
        raise AssertionError("cannot find the real-process switch")
    block = []
    for line in lines[header + 1:]:
        if line.strip() and not line.startswith(" "):
            break
        block.append(line)
    value = None
    for line in block:
        stripped = line.strip()
        prefix = key + ": "
        if stripped.startswith(prefix):
            value = stripped[len(prefix):]
            break
    if value != "'1'":
        raise AssertionError("cannot find the real-process switch")
    for gate, part_prefix in GATES:
        jobs = [gate, *(f"{part_prefix}_{index}" for index in range(1, 5))]
        for job in jobs:
            body = job_body(lines, job)
            if body is None or any(key in line for line in body):
                raise AssertionError("cannot find the real-process switch")
    for job_id in MACOS_PARTS:
        body = job_body(lines, job_id)
        if body is None or any(key in line for line in body):
            raise AssertionError("cannot find the real-process switch")
    return WorkflowSwitch(value)

def gate_scripts(text: str, reader: str = "auto") -> tuple[tuple[str, str], ...]:
    if reader == "auto":
        reader = gate_readers()[0]
    if reader == "yaml":
        return yaml_gate_scripts(text)
    if reader == "indent":
        return indent_gate_scripts(text)
    raise AssertionError(reader)

def yaml_gate_scripts(text: str) -> tuple[tuple[str, str], ...]:
    try:
        import yaml
    except ImportError:
        raise AssertionError("cannot find the result block") from None
    loaded = yaml.safe_load(text)
    jobs = loaded.get("jobs") if isinstance(loaded, dict) else None
    if not isinstance(jobs, dict):
        raise AssertionError("cannot find the gate job")
    scripts = []
    for gate, prefix in GATES:
        job = jobs.get(gate)
        if not isinstance(job, dict):
            raise AssertionError("cannot find gate job " + gate)
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise AssertionError("cannot find the result block of gate job " + gate)
        needle = "${{ needs." + prefix + "_"
        found = []
        for step in steps:
            run = step.get("run") if isinstance(step, dict) else None
            if isinstance(run, str) and needle in run:
                found.append(run if run.endswith("\n") else run + "\n")
        if len(found) != 1:
            raise AssertionError("cannot find the result block of gate job " + gate)
        scripts.append((prefix, found[0]))
    return tuple(scripts)

def indent_gate_scripts(text: str) -> tuple[tuple[str, str], ...]:
    lines = text.splitlines(keepends=True)
    scripts = []
    for gate, prefix in GATES:
        body = job_body(lines, gate)
        if body is None:
            raise AssertionError("cannot find gate job " + gate)
        if any("\t" in line for line in body):
            raise AssertionError("cannot find the result block of gate job " + gate)
        needle = "${{ needs." + prefix + "_"
        blocks = [block for block in literal_run_blocks(body) if needle in block]
        if len(blocks) != 1:
            raise AssertionError("cannot find the result block of gate job " + gate)
        scripts.append((prefix, blocks[0]))
    return tuple(scripts)

def macos_part_run(index: int) -> str:
    return (
        'log="$RUNNER_TEMP/macos-312.txt"\n'
        ': > "$log"\n'
        "status=0\n"
        f'python3 scripts/run_tests.py --part {index}/4 >> "$log" 2>&1 || status=1\n'
        'cat "$log" || true\n'
        "{\n"
        "  grep -E '^Ran [0-9]+ tests? in ' \"$log\" || true\n"
        "  grep -E '^(OK|FAILED)' \"$log\" || true\n"
        "  grep -E '^(FAIL|ERROR): ' \"$log\" || true\n"
        '} >> "$GITHUB_STEP_SUMMARY"\n'
        'exit "$status"\n'
    )

def job_body(lines: list[str], gate: str) -> list[str] | None:
    header = None
    for index, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if stripped.startswith(" ") and stripped.strip() == gate + ":":
            header = index
            break
    if header is None:
        return None
    indent = len(lines[header]) - len(lines[header].lstrip(" "))
    body = []
    for line in lines[header + 1:]:
        if line.strip() and (len(line) - len(line.lstrip(" "))) <= indent:
            break
        body.append(line)
    return body

def literal_run_blocks(body: list[str]) -> list[str]:
    blocks = []
    index = 0
    while index < len(body):
        stripped = body[index].rstrip("\n").lstrip(" ")
        marker = stripped[2:] if stripped.startswith("- ") else stripped
        if marker == "run: |":
            parent = len(body[index]) - len(body[index].lstrip(" "))
            index += 1
            raw = []
            while index < len(body):
                nxt = body[index]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip(" "))) <= parent:
                    break
                raw.append(nxt)
                index += 1
            blocks.append(dedent_block(raw))
            continue
        index += 1
    return blocks

def dedent_block(raw: list[str]) -> str:
    content = None
    for line in raw:
        if line.strip():
            content = len(line) - len(line.lstrip(" "))
            break
    if content is None:
        raise AssertionError("cannot find the result block")
    kept = []
    for line in raw:
        if not line.strip():
            kept.append("\n")
            continue
        width = len(line) - len(line.lstrip(" "))
        if width < content:
            raise AssertionError("cannot find the result block")
        kept.append(line[content:])
    return "".join(kept)

def result_block_span(lines: list[str], prefix: str) -> tuple[int, int, int, str]:
    needle = "${{ needs." + prefix + "_1.result }}"
    starts = [index for index, line in enumerate(lines) if needle in line]
    if len(starts) != 1:
        raise AssertionError("cannot find the result block of " + prefix)
    start = starts[0]
    stop = start + 1
    while stop < len(lines) and ("${{ needs." + prefix + "_") in lines[stop]:
        stop += 1
    if stop - start != 4:
        raise AssertionError("cannot find the result block of " + prefix)
    indent = len(lines[start]) - len(lines[start].lstrip(" "))
    block = "".join(line[indent:] for line in lines[start:stop])
    return start, stop, indent, block

def result_block(text: str, prefix: str) -> str:
    lines = text.splitlines(keepends=True)
    _start, _stop, _indent, block = result_block_span(lines, prefix)
    return block

def mutate_gate_block(name: str, block: str) -> str:
    lines = block.splitlines(keepends=True)
    if name == "or_true":
        return "".join(line.rstrip("\n") + " || true\n" for line in lines)
    if name == "set_plus_e":
        return "set +e\n" + block
    if name == "if_false":
        return "if false; then\n" + "".join("  " + line for line in lines) + "fi\n"
    raise AssertionError(name)

def workflow_with_gate_mutation(text: str, name: str) -> str:
    lines = text.splitlines(keepends=True)
    for prefix in ("part_310", "part_312"):
        start, stop, indent, block = result_block_span(lines, prefix)
        mutated = mutate_gate_block(name, block)
        replacement = []
        for line in mutated.splitlines(keepends=True):
            body = line if line.endswith("\n") else line + "\n"
            replacement.append((" " * indent + body) if body.strip() else "\n")
        lines[start:stop] = replacement
    return "".join(lines)

def report_failed_part(script: str, prefix: str) -> None:
    if "macos_312" in script:
        status = gate_script_status(script, prefix, ("failure", "success", "success", "success"), macos=("success",) * 4)
    else:
        status = gate_script_status(script, prefix, ("failure", "success", "success", "success"))
    if status == 0:
        raise AssertionError("gate exited 0 with a failed part")

def gate_script_status(
    script: str, prefix: str, results: tuple[str, ...], macos: tuple[str, ...] | None = None
) -> int:
    body = script
    for index, result in enumerate(results, start=1):
        token = "${{ needs." + prefix + "_" + str(index) + ".result }}"
        if token not in body:
            raise AssertionError(token)
        body = body.replace(token, result, 1)
    if "${{ needs.real_312.result }}" in body:
        raise AssertionError("real_312")
    macos_tokens = ["${{ needs." + job_id + ".result }}" for job_id in MACOS_PARTS]
    has_macos_tokens = any(token in body for token in macos_tokens)
    if has_macos_tokens and macos is None:
        raise AssertionError("macos results missing: pass macos results")
    if not has_macos_tokens and macos is not None:
        raise AssertionError("macos results passed but no macos tokens")
    if macos is not None:
        if len(macos) != len(MACOS_PARTS):
            raise AssertionError("macos results length")
        for job_id, result in zip(MACOS_PARTS, macos):
            token = "${{ needs." + job_id + ".result }}"
            if token not in body:
                raise AssertionError(token)
            while token in body:
                body = body.replace(token, result, 1)
    if "${{" in body:
        raise AssertionError(body)
    completed = subprocess.run(["bash", "-e", "-c", body], capture_output=True, text=True)
    return completed.returncode

