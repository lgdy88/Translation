"""Compare reconstructed translator.py bytecode against original pyc, per function."""
import marshal
import types
import dis
import sys

ORIG = r"C:\Users\Lenovo\ZCodeProject\translator-re\extracted\scripts\translator.pyc"
MINE = r"C:\Users\Lenovo\ZCodeProject\translator-re\translator.py"

orig = marshal.loads(open(ORIG, "rb").read()[16:])
mine = compile(open(MINE, encoding="utf-8").read(), "translator.py", "exec")

JUMP_OPS = {
    "JUMP_FORWARD", "JUMP_BACKWARD", "POP_JUMP_FORWARD_IF_FALSE",
    "POP_JUMP_FORWARD_IF_TRUE", "POP_JUMP_FORWARD_IF_NONE",
    "POP_JUMP_BACKWARD_IF_FALSE", "POP_JUMP_BACKWARD_IF_TRUE",
    "POP_JUMP_BACKWARD_IF_NONE", "JUMP_IF_TRUE_OR_POP",
    "JUMP_IF_FALSE_OR_POP", "FOR_ITER", "SEND",
}


def normalize(code):
    """Return list of comparable tokens for a code object body (excl. nested)."""
    ins = list(dis.get_instructions(code))
    offset_to_idx = {i.offset: n for n, i in enumerate(ins)}
    toks = []
    for i in ins:
        if i.opname in ("RESUME", "CACHE", "PRECALL", "MAKE_CELL", "COPY_FREE_VARS"):
            continue  # version-noise ops / cell setup derived from structure
        arg = i.argrepr
        if arg.startswith("<code object"):
            arg = "<code>"
        if i.opname in JUMP_OPS:
            tgt = offset_to_idx.get(i.argval, "?")
            arg = f"->{tgt}"
        if i.opname in ("LOAD_GLOBAL",):
            arg = i.argrepr.replace("NULL + ", "")
        toks.append((i.opname, arg))
    return toks


def collect(code, out, path):
    out[path] = code
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            collect(c, out, f"{path}.{c.co_name}")


a, b = {}, {}
collect(orig, a, "mod")
collect(mine, b, "mod")

names_a = set(a)
names_b = set(b)
only_a = names_a - names_b
only_b = names_b - names_a
if only_a:
    print("MISSING in reconstruction:", sorted(only_a))
if only_b:
    print("EXTRA in reconstruction:", sorted(only_b))

total_mismatch = 0
diff_dump = open(r"C:\Users\Lenovo\ZCodeProject\translator-re\diff_dump.txt", "w", encoding="utf-8")
for name in sorted(names_a & names_b):
    ta, tb = normalize(a[name]), normalize(b[name])
    if ta == tb:
        print(f"OK      {name}  ({len(ta)} instrs)")
        continue
    total_mismatch += 1
    print(f"DIFF    {name}  orig={len(ta)} mine={len(tb)}")
    diff_dump.write(f"===== {name} =====\n")
    for i in range(max(len(ta), len(tb))):
        va = ta[i] if i < len(ta) else ("<none>", "")
        vb = tb[i] if i < len(tb) else ("<none>", "")
        mark = "  " if va == vb else "**"
        diff_dump.write(f"{mark} [{i:4d}] {str(va):55s} | {vb}\n")

print(f"\n{'ALL MATCH' if total_mismatch == 0 and not only_a and not only_b else f'{total_mismatch} functions differ'}")
print(f"python used: {sys.version.split()[0]}")
