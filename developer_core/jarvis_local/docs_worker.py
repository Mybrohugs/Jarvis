"""Isolated helper for documents from disk. Run by path with -I and a timeout; prints one JSON line.

  info  <pdf>                    -> {"pages": n, "text": "..."} (text capped)
  merge <out.pdf> <a.pdf> <b.pdf> ...  -> {"pages": n}   (writes out.pdf; it must not exist)
  topdf <in.docx> <out.pdf>      -> {"pages": n}   (Word itself, separate hidden instance, read-only)

A malformed or hostile PDF can only hang or crash this process, never JARVIS.
"""
import json
import os
import sys

MAX_TEXT = 60_000


def info(path):
    from pypdf import PdfReader
    r = PdfReader(path)
    if r.is_encrypted:
        try:
            r.decrypt('')
        except Exception:
            return {'error': 'encrypted'}
    out, size = [], 0
    for page in r.pages:
        t = page.extract_text() or ''
        out.append(t)
        size += len(t)
        if size > MAX_TEXT:
            break
    return {'pages': len(r.pages), 'text': '\n'.join(out)[:MAX_TEXT]}


def merge(out, parts):
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for p in parts:
        r = PdfReader(p)
        if r.is_encrypted:
            return {'error': 'encrypted: ' + os.path.basename(p)}
        for page in r.pages:
            w.add_page(page)
    with open(out, 'xb') as f:
        w.write(f)
    return {'pages': len(PdfReader(out).pages)}


def topdf(src, out):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    word = win32com.client.DispatchEx('Word.Application')  # own instance: the owner's open Word is not touched
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(src, ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False, Visible=False)
        try:
            doc.ExportAsFixedFormat(out, 17)  # wdExportFormatPDF
        finally:
            doc.Close(False)
    finally:
        word.Quit()
    return info(out) | {'text': ''}


def main(argv):
    cmd = argv[1]
    if cmd == 'info':
        res = info(argv[2])
    elif cmd == 'merge':
        res = merge(argv[2], argv[3:])
    elif cmd == 'topdf':
        res = topdf(argv[2], argv[3])
    else:
        res = {'error': 'unknown command'}
    emit(res)


def emit(obj):  # UTF-8 bytes: under -I Python ignores PYTHONIOENCODING and would write the console code page
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    try:
        main(sys.argv)
    except Exception as exc:  # reported as data; the caller decides
        emit({'error': type(exc).__name__ + ': ' + str(exc)[:200]})
