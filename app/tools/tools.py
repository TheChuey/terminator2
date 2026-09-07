"""
app/tools/tools.py
==================

Every executable tool in the application, consolidated into one module.

One function per tool; each function's docstring is what the LLM "sees":
PromptManager turns the first line into the system prompt's AVAILABLE TOOLS
section, and Ollama derives the JSON tool schema from the function name,
signature, types, and docstring. Keep them precise and self-describing.

Registered tools (IDs in agent.json):
    map_files, read_file, write_text_file, delete_files,
    get_current_date, tell_me_the_date_and_time, search_chat_logs

The shared FileSession lives in app/tools/state.py.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# READ - Docling-powered document reader (IBM Docling)
# ---------------------------------------------------------------------------

# Plain-text formats are read straight off disk (fast path). Everything else
# (PDF/DOCX/PPTX/XLSX/HTML/images/...) goes through IBM Docling's pipeline,
# which returns clean, structurally-formatted markdown.
_PLAIN_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".log", ".text",
    ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx",
    ".css", ".scss", ".sass", ".xml", ".tex", ".rst",
}


def _is_plain_text(path: Path) -> bool:
    """True for files whose raw text is already the well-formatted content."""
    return path.suffix.lower() in _PLAIN_TEXT_EXTENSIONS


_DOCLING_CONVERTERS = {}


def _docling_converter(ocr: bool):
    """Return a cached, lazily-created Docling DocumentConverter.

    The converter is created once per ocr setting and reused across calls so
    the (expensive) pipeline + model artifacts are initialized only once.
    First-ever conversion downloads the layout/OCR models from HuggingFace.
    """
    if ocr not in _DOCLING_CONVERTERS:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError:
            _DOCLING_CONVERTERS[ocr] = None
            return None

        if ocr:
            options = PdfPipelineOptions(do_ocr=True)
            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        else:
            converter = DocumentConverter()

        _DOCLING_CONVERTERS[ocr] = converter
    return _DOCLING_CONVERTERS[ocr]


def read_file(path: str, ocr: bool = True) -> dict:
    """Reads a file and returns its content as well-formatted text (markdown).

    Use this tool whenever you need the contents of a document, source file,
    or any file on disk. It returns the extracted content ready to use.

    Plain text and code files (.txt, .md, .log, .json, source code, ...) are
    read directly off disk. All other formats - PDF, DOCX, PPTX, XLSX, HTML,
    images, and more - are converted by IBM Docling into clean, structured
    markdown that preserves headings, tables, and layout. OCR is enabled by
    default so scanned PDFs are handled too.

    Args:
        path (str): Absolute path to the file to read.
        ocr (bool): When True (default), optical character recognition is
            enabled for PDFs so scanned/rotated pages can be read.

    Returns:
        dict: {"success": bool, "tool": "read_file", "data": {...}, "error": str|None}
            data keys: path, filename, file_type, extracted_content, status
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return {
            "success": False,
            "tool": "read_file",
            "data": {},
            "error": f"File '{path}' not found."
        }

    try:
        if _is_plain_text(p):
            content = p.read_text(encoding="utf-8", errors="replace")
            status = "success"
        else:
            converter = _docling_converter(ocr)
            if converter is None:
                return {
                    "success": False,
                    "tool": "read_file",
                    "data": {},
                    "error": "Docling is not installed. Install it with `pip install docling` "
                             "to read PDF/DOCX/PPTX/XLSX/HTML/image files.",
                }

            from docling.datamodel.base_models import ConversionStatus

            result = converter.convert(str(p), max_num_pages=400)
            if result.status is ConversionStatus.FAILURE:
                errors = "; ".join(e.error_message for e in getattr(result, "errors", []))
                return {
                    "success": False,
                    "tool": "read_file",
                    "data": {"path": str(p), "filename": p.name, "file_type": p.suffix.lower()},
                    "error": errors or "Docling could not convert the document.",
                }

            content = result.document.export_to_markdown()
            status = "success" if result.status is ConversionStatus.SUCCESS else "partial_success"

        return {
            "success": True,
            "tool": "read_file",
            "data": {
                "path": str(p),
                "filename": p.name,
                "file_type": p.suffix.lower(),
                "extracted_content": content,
                "status": status,
            },
            "error": None,
        }
    except Exception as e:
        return {"success": False, "tool": "read_file", "data": {}, "error": str(e)}


# ---------------------------------------------------------------------------
# MAP - directory inspection
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode"}


def map_files(path: str, max_depth: int = 8, max_entries: int = 5000) -> dict:
    """Inspects a directory and returns a structured list of its files and folders.

    Use this tool to see what exists on disk before reading, writing, or
    deleting anything. Returns every file and subfolder under the given
    directory (to max_depth), excluding ordinary noise like .git, .venv, and
    __pycache__. Folders are listed with their subpaths so you know exactly
    where a file lives before you touch it.

    Args:
        path (str): The directory to inspect.
        max_depth (int): Maximum subdirectory depth to descend into (default 8).
        max_entries (int): Maximum number of entries to return (default 5000).

    Returns:
        dict: {"success": bool, "tool": "map_files", "data": {...}, "error": str|None}
            data keys: files (list of {name, path, extension, type, parent, level}),
                        truncated (bool), max_entries (int)
    """
    root = Path(path)
    if not root.exists() or not root.is_dir():
        return {
            "success": False,
            "tool": "map_files",
            "data": {},
            "error": f"Path '{path}' is not a valid directory."
        }

    files_data = []
    for current_dir, dirs, files in os.walk(root, topdown=True):
        depth = len(Path(current_dir).relative_to(root).parts)
        if depth >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]

        for d in dirs:
            if len(files_data) >= max_entries:
                break
            full = Path(current_dir) / d
            files_data.append({
                "name": d,
                "path": str(full),
                "extension": "",
                "type": "directory",
                "parent": Path(current_dir).name if Path(current_dir) != root else "",
                "level": depth + 1,
            })

        for f in files:
            if len(files_data) >= max_entries:
                break
            full = Path(current_dir) / f
            files_data.append({
                "name": f,
                "path": str(full),
                "extension": Path(f).suffix,
                "type": "file",
                "parent": Path(current_dir).name if Path(current_dir) != root else "",
                "level": depth + 1,
            })

        if len(files_data) >= max_entries:
            break

    truncated = len(files_data) >= max_entries
    return {
        "success": True,
        "tool": "map_files",
        "data": {
            "files": files_data,
            "truncated": truncated,
            "max_entries": max_entries,
        },
        "error": None,
    }


# ---------------------------------------------------------------------------
# WRITE - file creation
# ---------------------------------------------------------------------------

def write_text_file(name: str, content: str, output_path: str, overwrite: bool = False) -> dict:
    """Creates a text file containing the given content.

    Use this tool to save any text or code you have produced to disk. The
    parent directory is created automatically, so you do not need a separate
    "create folder" step. By default an existing file with the same name is
    NOT overwritten - pass overwrite=True when you intentionally want to.

    Args:
        name (str): File name to write, e.g. "summary.txt".
        content (str): Full text content to write into the file.
        output_path (str): Directory in which to create the file.
        overwrite (bool): Whether to overwrite the file if it already exists
            (default False).

    Returns:
        dict: {"success": bool, "tool": "write_text_file", "data": {...}, "error": str|None}
            data keys: filename, path, type, size, status (built on success)
    """
    if not name or content is None or not output_path:
        return {
            "success": False,
            "tool": "write_text_file",
            "data": {},
            "error": "Missing required arguments. Need name (file name), content (text), and output_path (folder)."
        }

    try:
        out_dir = Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / name

        if file_path.exists() and not overwrite:
            return {
                "success": False,
                "tool": "write_text_file",
                "data": {},
                "error": f"File '{file_path}' already exists and overwrite is set to False."
            }

        file_path.write_text(content, encoding="utf-8")
        return {
            "success": True,
            "tool": "write_text_file",
            "data": {
                "filename": name,
                "path": str(file_path),
                "type": "text/plain",
                "size": file_path.stat().st_size,
                "status": "written",
            },
            "error": None,
        }
    except Exception as e:
        return {"success": False, "tool": "write_text_file", "data": {}, "error": str(e)}


# ---------------------------------------------------------------------------
# DELETE - two-step approval-safe deletion
# ---------------------------------------------------------------------------

def delete_files(file_list: list, approved: bool = False) -> dict:
    """Deletes files ONLY after explicit approval has been given.

    Deleting is permanent. Calling this tool with approved=False (the safe
    default) only PREPARES the deletion. Call it a second time with
    approved=True to actually remove the files; the runtime additionally
    blocks any path that was never proposed in the first (approved=False) call.

    Args:
        file_list (list): List of file paths to delete.
        approved (bool): Must be True to actually delete. False only records
            the pending request (two-step confirmation).

    Returns:
        dict: {"success": bool, "tool": "delete_files", "data": {...}, "error": str|None}
            data keys: results (path -> "deleted"/"file_not_found"/"error: ..."),
                        or pending_files (list) when approval is still required
    """
    if not file_list:
        return {
            "success": False,
            "tool": "delete_files",
            "data": {},
            "error": "No files provided for deletion."
        }

    if not approved:
        return {
            "success": False,
            "tool": "delete_files",
            "data": {"pending_files": file_list},
            "error": "Deletion requires explicit approval. Set approved=True to finalize."
        }

    results = {}
    for f_path in file_list:
        p = Path(f_path)
        if p.exists() and p.is_file():
            try:
                p.unlink()
                results[f_path] = "deleted" if not p.exists() else "failed_to_verify"
            except Exception as e:
                results[f_path] = f"error: {str(e)}"
        else:
            results[f_path] = "file_not_found"

    all_success = all(v == "deleted" for v in results.values())
    return {
        "success": all_success,
        "tool": "delete_files",
        "data": {"results": results},
        "error": None if all_success else "One or more files failed to delete.",
    }


# ---------------------------------------------------------------------------
# DATE / TIME
# ---------------------------------------------------------------------------

def get_current_date() -> str:
    """Returns the real current calendar date (e.g. 'Monday, January 05, 2026').

    Use this tool when you need to know today's date - for example when a
    user asks "what day is it", when dating a response, or when reasoning
    about relative dates. No arguments.
    """
    from datetime import datetime
    return datetime.now().strftime("%A, %B %d, %Y")


def tell_me_the_date_and_time() -> str:
    """Returns the current date and time down to the second.

    Use this tool for anything needing the moment now (date + time), like
    timestamps, "what time is it", or checking elapsed time. No arguments.
    """
    from datetime import datetime
    now = datetime.now()
    return f"The current date and time is {now.strftime('%Y-%m-%d %H:%M:%S')}"


# ---------------------------------------------------------------------------
# SEARCH - chat transcript / RAG memory recall
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import paths  # noqa: E402

RAG_DB_DIR = paths.RAG_DB_DIR
CHAT_RECORDS_DIR = paths.CHAT_RECORDS_DIR

_rag_ingested = False


def _auto_ingest_enabled() -> bool:
    """The `rag.autoIngest` toggle: None of the UI config can bulk-ingest ALL
    transcripts when the store is empty. Off -> only explicit commits (per-chat
    "save to memory") or a rebuild populate the store."""
    return bool(paths.rag_config()["autoIngest"])


def _ensure_rag_ingested(storage) -> bool:
    """Index the chat transcripts into the persistent store once, when the
    store is empty AND auto-ingest is enabled.

    Returns True when this process has already ingested (or just successfully
    ingested) the transcripts; False when there was nothing to ingest or the
    feature is disabled.
    """
    global _rag_ingested
    if _rag_ingested:
        return True
    if not _auto_ingest_enabled():
        return False

    from rag.ingest import ingest_directory

    chunks = ingest_directory(str(CHAT_RECORDS_DIR))
    if chunks:
        storage.add_chunks(chunks)
        print(f"[RAG] Auto-ingested {len(chunks)} segment(s) from {CHAT_RECORDS_DIR}")
    _rag_ingested = True
    return bool(chunks)


def search_chat_logs(query: str) -> str:
    """Searches past chat transcripts for a keyword and returns the matching segments.

    Use this tool to recall what was discussed in earlier conversations: this
    is the agent's long-term memory. It searches the saved chat records and
    returns the most relevant segments with their session title, speaker,
    date, and content.

    Args:
        query (str): The search keyword, term, or phrase to look up.

    Returns:
        str: Formatted search results ('' when nothing matches).
    """
    try:
        from rag.search import RAGStorage

        storage = RAGStorage(persist_dir=str(RAG_DB_DIR))

        results = storage.query(query, n_results=3)

        if not results:
            if _ensure_rag_ingested(storage):
                results = storage.query(query, n_results=3)

        if not results:
            return f"No matches found in your chat transcripts for the query: '{query}'."

        formatted_results = [f"--- RAG SEARCH RESULTS FOR: '{query}' ---"]
        for idx, r in enumerate(results):
            meta = r["metadata"]
            session_title = meta.get("session_title") or meta.get("source_file", "Untitled Chat")
            speaker = meta.get("speaker", "Unknown")
            date = meta.get("date", "Unknown Date")

            formatted_results.append(
                f"Result [{idx + 1}]:\n"
                f"  Session: {session_title}\n"
                f"  Speaker: {speaker} | Date: {date}\n"
                f"  Content: {r['text']}\n"
                f"----------------------------------------"
            )

        return "\n\n".join(formatted_results)

    except ImportError:
        return "Error: RAG engine modules not found. Ensure the 'rag/' folder is present in your project root."
    except Exception as e:
        return f"Error executing chat log search: {e}"