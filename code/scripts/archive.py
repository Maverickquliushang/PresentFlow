"""Archive an explicit source allowlist; never collect private runs or machine settings."""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_DIRS = ('docs', 'code/src', 'code/tests', 'code/skills', 'code/examples', 'code/scripts')
ALLOWED_FILES = ('README.md', 'LICENSE', 'AGENTS.md', 'CHANGELOG.md', '.gitignore', 'project-env.ps1',
                 'code/pyproject.toml', 'code/uv.lock', 'code/project-env.ps1', 'code/PYTHON_ENVIRONMENT.md', 'code/MANIFEST.in', 'code/LICENSE')


def archive(output):
    output = Path(output)
    if output.exists():
        raise ValueError('Archive already exists; choose a new filename.')
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [ROOT / name for name in ALLOWED_FILES if (ROOT / name).is_file()]
    for directory in ALLOWED_DIRS:
        files.extend(p for p in (ROOT / directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and not any(part.endswith('.egg-info') for part in p.parts)
                     and not p.name.endswith(('.pyc', '.local.json')))
    with ZipFile(output, 'x', ZIP_DEFLATED) as package:
        for file in sorted(set(files)):
            package.write(file, 'PresentFlow/' + file.relative_to(ROOT).as_posix())
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    print(archive(parser.parse_args().output))
