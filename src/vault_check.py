"""Valida o vault: frontmatter obrigatório e [[links]] que apontam pra nota existente."""
import re
import sys
from pathlib import Path

TYPES = {"pessoa", "projeto", "cliente", "diario", "ideia", "conceito", "eu", "objetivos", "regras", "tarefas", "agenda"}
FM = re.compile(r"^---\n(.*?)\n---\n", re.S)
LINK = re.compile(r"\[\[([^\]|#]+)")


def check(root: Path) -> list[str]:
    notes = {p.stem: p for p in root.rglob("*.md")}
    errors = []
    for stem, path in notes.items():
        text = path.read_text(encoding="utf-8")
        m = FM.match(text)
        if not m:
            errors.append(f"{path}: sem frontmatter")
            continue
        fm = dict(l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)
        fm = {k.strip(): v.strip() for k, v in fm.items()}
        if fm.get("type") not in TYPES:
            errors.append(f"{path}: type inválido {fm.get('type')!r}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fm.get("updated", "")):
            errors.append(f"{path}: updated inválido {fm.get('updated')!r}")
        for target in LINK.findall(text):
            if target.strip() not in notes:
                errors.append(f"{path}: link quebrado [[{target}]]")
    return errors


def demo():
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "A.md").write_text("---\ntype: pessoa\nupdated: 2026-09-18\n---\n# A\nvê [[B]] e [[Nada]]", encoding="utf-8")
    (d / "B.md").write_text("sem frontmatter", encoding="utf-8")
    errs = check(d)
    assert any("link quebrado [[Nada]]" in e for e in errs), errs
    assert any("B.md: sem frontmatter" in e for e in errs), errs
    assert not any("[[B]]" in e for e in errs), errs
    print("demo ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
        sys.exit(0)
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "vault")
    errs = check(root)
    total = len(list(root.rglob("*.md")))
    print("\n".join(errs) or f"vault ok: {total} notas")
    sys.exit(1 if errs else 0)
