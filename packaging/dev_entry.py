from pathlib import Path
import sys
if not getattr(sys,'frozen',False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from developer_core.jarvis_local.dev_desktop import main
if __name__=='__main__':
    main()
