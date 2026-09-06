# ingest.py - Part of the Terminator Stateful Agentic RAG System
# Slices long chat logs into semantic chunks with a sliding window buffer.

import os
import re
from pathlib import Path

def slide_overlap_chunk(text, chunk_size=500, overlap=50):
    """Slices a text stream into character segments with a sliding overlap.
    
    This preserves semantic context across boundaries and prevents sentence-cutting.
    """
    chunks = []
    if not text:
        return chunks
    step = chunk_size - overlap
    if step <= 0:
        step = chunk_size  # Safety check
        
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        if end >= len(text):
            break
        start += step
    return chunks

def parse_chat_log(file_content):
    """Extracts session metadata and individual speaker turns from a chat log."""
    header_info = {}
    lines = file_content.splitlines()
    
    # Header keys map to metadata fields
    header_fields = {
        "title": [r"^Session:\s*(.+)$", r"^TITLE:\s*(.+)$"],
        "agent": [r"^Agent:\s*(.+)$", r"^AGENT:\s*(.+)$"],
        "model": [r"^Model:\s*(.+)$", r"^MODEL:\s*(.+)$"],
        "date": [r"^Date:\s*(.+)$", r"^STARTED:\s*(.+)$"],
    }
    
    for line in lines:
        for field, patterns in header_fields.items():
            if field in header_info:
                continue
            for pattern in patterns:
                match = re.match(pattern, line.strip())
                if match:
                    header_info[field] = match.group(1).strip()
                    break

    turns = []
    turn_pattern = re.compile(r"^\[([^\]]+)\]\s+(.*)$")
    
    current_speaker = None
    current_date = None
    current_body_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        match = turn_pattern.match(line.strip())
        if match:
            # Save preceding turn
            if current_speaker is not None and current_body_lines:
                turns.append({
                    "speaker": current_speaker,
                    "date": current_date,
                    "content": "\n".join(current_body_lines).strip()
                })
            current_speaker = match.group(1).strip()
            current_date = match.group(2).strip()
            current_body_lines = []
            # Skip separator line
            if i + 1 < len(lines) and re.match(r"^[-=]+$", lines[i+1].strip()):
                i += 1
        else:
            if current_speaker is not None:
                # Exclude horizontal rule dividers
                if not re.match(r"^={10,}$", line.strip()) and not re.match(r"^-{10,}$", line.strip()):
                    current_body_lines.append(line)
        i += 1
        
    # Append the last turn
    if current_speaker is not None and current_body_lines:
        turns.append({
            "speaker": current_speaker,
            "date": current_date,
            "content": "\n".join(current_body_lines).strip()
        })
        
    return header_info, turns

def ingest_file(file_path, chunk_size=500, overlap=50):
    """Index ONE transcript file into chunked sub-documents (used for
    per-chat commits and directory ingestion alike)."""
    txt_file = Path(file_path)
    if not txt_file.exists() or not txt_file.is_file():
        print(f"[INGEST] Warning: File {file_path} does not exist.")
        return []

    chunks_out = []
    try:
        content = txt_file.read_text(encoding="utf-8", errors="replace")
        header, turns = parse_chat_log(content)

        # Default values if not found in header
        title = header.get("title") or txt_file.stem.replace("-", " ").title()
        agent = header.get("agent") or "Unknown Agent"
        model = header.get("model") or "Unknown Model"
        date = header.get("date") or "Unknown Date"

        for turn_idx, turn in enumerate(turns):
            speaker = turn["speaker"]
            turn_date = turn["date"] or date
            text = turn["content"]

            # Perform sliding overlap chunking on the turn's content
            chunks = slide_overlap_chunk(text, chunk_size, overlap)

            for chunk_idx, chunk_text in enumerate(chunks):
                # Combine context so the text chunk carries context
                source_label = f"[{title} | Turn {turn_idx + 1} | {speaker}]"
                contextual_text = f"{source_label} {chunk_text}"

                metadata = {
                    "source_file": txt_file.name,
                    "session_title": title,
                    "agent": agent,
                    "model": model,
                    "speaker": speaker,
                    "date": turn_date,
                    "turn_index": turn_idx,
                    "chunk_index": chunk_idx,
                    "raw_chunk": chunk_text  # Added to prevent key errors
                }

                chunks_out.append({
                    "text": contextual_text,
                    "raw_chunk": chunk_text,
                    "metadata": metadata
                })
    except Exception as e:
        print(f"[INGEST] Error reading {txt_file.name}: {e}")
    return chunks_out

def ingest_directory(directory_path, chunk_size=500, overlap=50):
    """Scans directory_path for .txt logs and extracts chunked documents with metadata."""
    dir_path = Path(directory_path)
    if not dir_path.exists() or not dir_path.is_dir():
        print(f"[INGEST] Warning: Directory {directory_path} does not exist.")
        return []

    all_chunks = []
    txt_files = list(dir_path.glob("*.txt"))
    print(f"[INGEST] Found {len(txt_files)} chat transcript(s) in {directory_path}")

    for txt_file in txt_files:
        all_chunks.extend(ingest_file(txt_file, chunk_size=chunk_size, overlap=overlap))

    print(f"[INGEST] Completed: Chunked chat logs into {len(all_chunks)} sub-documents.")
    return all_chunks

if __name__ == "__main__":
    # Test block
    test_log = """
================================================================
Session: Test Session
Agent:   Dev Assistant
Model:   gemma4:e2b
Date:    Sep 04, 2026, 02:35 PM
Interactions between LLM and user: 1
================================================================

[You]  Sep 04, 2026, 02:35 PM
----------------------------------------------------------------
This is some test text. It should slide overlap nicely. Line 2.

[Dev Assistant]  Sep 04, 2026, 02:36 PM
----------------------------------------------------------------
Understood, Jesus. I am checking the RAG prototype integration.
"""
    header, turns = parse_chat_log(test_log)
    print("Parsed Header:", header)
    print("Parsed Turns:", len(turns))
    for t in turns:
        print(f"Turn by {t['speaker']}: {t['content']}")
        chunks = slide_overlap_chunk(t['content'], chunk_size=30, overlap=10)
        print("  Chunks:", chunks)
