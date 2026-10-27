"""Office copy editor. Runs ONLY inside the isolated V8 interpreter with `-I`.

Self-contained on purpose (no developer_core imports). Paths arrive already
validated by the DEV router; this worker re-checks source hash and destination
absence, writes a temp file, reloads it to verify, then creates the destination
exclusively. The original document is only ever opened for reading.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def probe():
    from importlib.metadata import version
    import docx  # noqa: F401
    import openpyxl  # noqa: F401
    import defusedxml  # noqa: F401
    return {'status': 'PASS', 'packages': {k: version(k) for k in ('openpyxl', 'python-docx', 'defusedxml')}}


def edit(src, tmp, op, payload):
    if op == 'docx-append-paragraph':
        from docx import Document
        doc = Document(src)
        doc.add_paragraph(payload['text'])
        doc.save(tmp)
        texts = [p.text for p in Document(tmp).paragraphs]
        return bool(texts) and texts[-1] == payload['text']
    from openpyxl import load_workbook
    wb = load_workbook(src, keep_links=False)
    try:
        ws = wb[payload['sheet']] if payload.get('sheet') else wb.active
        sheet = ws.title
        if op == 'xlsx-append-row':
            ws.append(payload['cells'])
            row = ws.max_row
        elif op == 'xlsx-set-cell':
            ws[payload['cell']] = payload['value']
        else:
            raise ValueError('operation not allowlisted')
        wb.save(tmp)
    finally:
        wb.close()
    check = load_workbook(tmp, read_only=True, keep_links=False)
    try:
        ws = check[sheet]
        if op == 'xlsx-append-row':
            values = [c.value for c in next(ws.iter_rows(min_row=row, max_row=row, max_col=len(payload['cells'])))]
            return ['' if v is None else str(v) for v in values] == payload['cells']
        return str(ws[payload['cell']].value) == payload['value']
    finally:
        check.close()


def main():
    request = json.loads(sys.stdin.read(200_000))
    if request.get('op') == 'probe':
        return probe()
    src, dst = Path(request['src']), Path(request['dst'])
    if src.suffix.lower() != dst.suffix.lower() or dst.exists() or not dst.parent.is_dir():
        raise ValueError('invalid destination')
    if sha256(src) != request['source_sha256']:
        raise ValueError('source changed since plan')
    fd, tmp = tempfile.mkstemp(prefix='.~jarvis_tmp_', suffix=dst.suffix, dir=dst.parent)
    os.close(fd)
    try:
        verified = edit(src, tmp, request['op'], request['payload'])
        if not verified:
            raise ValueError('reload verification failed')
        with open(tmp, 'rb') as a, open(dst, 'xb') as b:
            b.write(a.read())
    finally:
        os.unlink(tmp)
    return {'status': 'PASS', 'dst_sha256': sha256(dst), 'source_sha256_after': sha256(src),
            'verified_by_reload': True}


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({'status': 'FAIL', 'error': type(exc).__name__ + ': ' + str(exc)[:300]}, ensure_ascii=True))
        sys.exit(1)
