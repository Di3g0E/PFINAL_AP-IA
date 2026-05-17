import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from src.data.database import init_db

if __name__ == '__main__':
    init_db()
    print('init_db ok')
