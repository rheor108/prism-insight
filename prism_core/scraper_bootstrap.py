"""Launch the isolated local scraper from either KR or US callers."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    python = Path(os.getenv('PRISM_CRAWL_PYTHON') or ROOT / '.venv-crawl4ai/bin/python')
    if not python.is_file():
        sys.exit('Local scraper runtime missing; see docs/LOCAL_SCRAPER.md')
    os.chdir(ROOT)
    os.environ.setdefault('CRAWL4_AI_BASE_DIRECTORY', str(ROOT / 'runtime/crawl4ai'))
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.venv-crawl4ai/browsers'))
    os.execv(str(python), [str(python), '-m', 'prism_core.local_scraper'])


if __name__ == '__main__':
    main()
