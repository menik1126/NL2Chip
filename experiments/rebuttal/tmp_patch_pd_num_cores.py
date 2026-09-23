from pathlib import Path

p = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919/agent/evaluator.py")
t = p.read_text()
old1 = """        config_content = (
            f"export DESIGN_NAME = {top_module}\\n"
            f"export PLATFORM = sky130hd\\n"
            f"export VERILOG_FILES = {container_sv}\\n"
            f"export SDC_FILE = /workspace/constraints.sdc\\n"
            f"export CORE_UTILIZATION = 5\\n"
            f"export CORE_ASPECT_RATIO = 1\\n"
            f"export CORE_MARGIN = 2\\n"
        )
"""
new1 = """        config_content = (
            f"export DESIGN_NAME = {top_module}\\n"
            f"export PLATFORM = sky130hd\\n"
            f"export VERILOG_FILES = {container_sv}\\n"
            f"export SDC_FILE = /workspace/constraints.sdc\\n"
            f"export NUM_CORES = 8\\n"
            f"export CORE_UTILIZATION = 5\\n"
            f"export CORE_ASPECT_RATIO = 1\\n"
            f"export CORE_MARGIN = 2\\n"
        )
"""
old2 = """        config_content = (
            f"export DESIGN_NAME = {top_module}\\n"
            f"export PLATFORM = sky130hd\\n"
            f"export VERILOG_FILES = {container_sv}\\n"
            f"export SDC_FILE = /workspace/constraints.sdc\\n"
            f"export DIE_AREA = 0 0 {die_side} {die_side}\\n"
            f"export CORE_AREA = {margin} {margin} {core_side} {core_side}\\n"
            f"export PLACE_DENSITY = 0.15\\n"
        )
"""
new2 = """        config_content = (
            f"export DESIGN_NAME = {top_module}\\n"
            f"export PLATFORM = sky130hd\\n"
            f"export VERILOG_FILES = {container_sv}\\n"
            f"export SDC_FILE = /workspace/constraints.sdc\\n"
            f"export NUM_CORES = 8\\n"
            f"export DIE_AREA = 0 0 {die_side} {die_side}\\n"
            f"export CORE_AREA = {margin} {margin} {core_side} {core_side}\\n"
            f"export PLACE_DENSITY = 0.15\\n"
        )
"""
if old1 not in t:
    raise SystemExit("synth config block missing")
if old2 not in t:
    raise SystemExit("pnr config block missing")
t = t.replace(old1, new1, 1).replace(old2, new2, 1)
if t.count("export NUM_CORES = 8") < 2:
    raise SystemExit("NUM_CORES not applied twice")
p.write_text(t)
print("patched NUM_CORES=8 in synth+pnr config.mk")
