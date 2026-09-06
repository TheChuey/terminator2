# main.py - Part of the Terminator Stateful Agentic RAG System
# Orchestrates the cognitive Plan-Execute-Evaluate Loop & Self-Correction retrieval loops.

import os
import re
import sys
from pathlib import Path

# Insert current folder on sys.path so modules find each other if dropped in a directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest import ingest_directory
from search import RAGStorage

def build_checklist(query):
    """Builds a heuristic target checklist of keywords to evaluate search completeness.
    
    Removes common stop words and retains semantic core terms.
    """
    stop_words = {
        "what", "is", "your", "who", "the", "user", "list", "all", "california", "about", 
        "how", "to", "do", "you", "a", "an", "and", "or", "of", "for", "with", "from", 
        "me", "tell", "show", "give", "explain", "why", "where", "when", "can", "could", 
        "would", "it", "should", "work", "in", "folder", "name", "where", "are", "path",
        "they", "will", "detect", "automatically", "please", "not"
    }
    # Keep alphanumeric characters
    words = re.findall(r"\b\w{3,}\b", query.lower())
    keywords = [w for w in words if w not in stop_words]
    
    # Fallback to general words if all filtered out
    if not keywords:
        keywords = [w for w in words if len(w) > 2]
        
    # Deduplicate and limit to 4 key items
    return list(dict.fromkeys(keywords))[:4]

def generate_llm_response(prompt, model="gemma4:e2b"):
    """Queries local Ollama client if active."""
    import ollama
    try:
        resp = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp["message"]["content"]
    except Exception:
        return None

def formulate_grounded_answer(query, results, model="gemma4:e2b"):
    """Formulates a comprehensive response cited against specific transcripts."""
    if not results:
        return "I could not find any matching information in your chat records."
        
    context_str = ""
    citations = []
    
    for idx, r in enumerate(results):
        meta = r["metadata"]
        cite_num = idx + 1
        source_info = f"[{meta['session_title']} | Turn {meta['turn_index'] + 1} | {meta['speaker']} | {meta['date']}]"
        context_str += f"\nCitation [{cite_num}]: {source_info}\nContent: {r['text']}\n"
        citations.append(f"[{cite_num}] {meta['session_title']} ({meta['speaker']} on {meta['date']})")
        
    # Prompt targeting strict source grounding with inline citation numbers
    ollama_prompt = f"""
You are an advanced RAG reasoning assistant on the Terminator1 project.
Use ONLY the following context blocks from Jesus's past chat transcripts to answer his query: '{query}'.

Every factual claim must be directly supported by the context.
Cite sources inline as [1], [2], etc., matching the context citation numbers.

Context:
{context_str}

If the context doesn't contain enough information to fully answer, say so honestly. Do not make up facts.
Formulate a concise, helpful, and 100% grounded answer.
"""
    
    llm_answer = None
    try:
        import ollama
        # Probe if Ollama is responsive
        ollama.list()
        llm_answer = generate_llm_response(ollama_prompt, model)
    except Exception:
        pass
        
    if llm_answer:
        return llm_answer.strip()
        
    # 🛡️ Solid rule-based summary fallback if Ollama is offline or model is missing
    summary = "Based on your chat transcripts, here are the most relevant findings:\n\n"
    for idx, r in enumerate(results):
        meta = r["metadata"]
        text_content = meta.get("raw_chunk", r["text"])
        summary += f"• From chat '{meta['session_title']}' ({meta['speaker']} on {meta['date']}):\n"
        summary += f"  \"{text_content}\" [Citation {idx + 1}]\n\n"
    
    summary += "Sources cited:\n"
    summary += "\n".join(f"  [{i+1}] {cite}" for i, cite in enumerate(citations))
    return summary

def run_cognitive_state_loop(storage, query, model="gemma4:e2b", debug=True):
    """Executes the Plan-Execute-Evaluate Loop with dynamic query rewriting.
    
    Retries retrieval up to 3 times if checklist coverage score is below 0.75.
    """
    checklist = build_checklist(query)
    current_query = query
    retrieved_docs = []
    
    print(f"\n[COGNITION] Target Checklist: {checklist}")
    
    for attempt in range(1, 4):
        results = storage.query(current_query, n_results=4)
        retrieved_docs = results
        
        # Evaluate checklist completeness
        covered_items = []
        for item in checklist:
            item_covered = False
            for r in results:
                if item.lower() in r["text"].lower() or item.lower() in r["metadata"]["session_title"].lower():
                    item_covered = True
                    break
            if item_covered:
                covered_items.append(item)
                
        uncovered_items = [item for item in checklist if item not in covered_items]
        coverage_score = len(covered_items) / len(checklist) if checklist else 1.0
        
        if debug:
            print(f"[COGNITION] Attempt {attempt}/3:")
            print(f"            Query: '{current_query}'")
            print(f"            Coverage: {coverage_score:.2f} ({len(covered_items)}/{len(checklist)} covered)")
            print(f"            Covered: {covered_items}")
            if uncovered_items:
                print(f"            Missing: {uncovered_items}")
                
        if coverage_score >= 0.75:
            print(f"[COGNITION] Success: Coverage {coverage_score:.2f} meets threshold (>= 0.75)!")
            break
            
        if attempt < 3 and uncovered_items:
            # Dynamically append uncovered aspects to the query to guide vector retrieval
            missing_terms_str = " ".join(uncovered_items)
            current_query = f"{query} {missing_terms_str}"
            print(f"[COGNITION] Coverage is low. Rewriting query to prioritize missing aspects.")
    else:
        print("[COGNITION] Max attempts reached. Continuing with best available context.")
        
    return formulate_grounded_answer(query, retrieved_docs, model)

def main():
    print("=================================================================")
    print("🤖 Terminator1 Stateful Agentic RAG CLI")
    print("=================================================================")

    # Resolve configured locations when running inside the Terminator1 app;
    # fall back to cwd-relative defaults when used standalone.
    try:
        from app import paths as app_paths
        default_chat_logs = str(app_paths.CHAT_RECORDS_DIR)
        default_persist = str(app_paths.RAG_DB_DIR)
    except Exception:
        default_chat_logs = "./data/chatlog/agent-text-records"
        default_persist = "./data/rag_db"

    print("This system searches your local chat records to answer queries.")

    chat_logs_path = input(f"Enter the chat logs folder path [{default_chat_logs}]: ").strip()
    if not chat_logs_path:
        chat_logs_path = default_chat_logs

    print(f"\n[SYSTEM] Initializing persistent storage...")
    storage = RAGStorage(persist_dir=default_persist)
    
    print(f"[SYSTEM] Scanning and parsing files in '{chat_logs_path}'...")
    chunks = ingest_directory(chat_logs_path)
    
    if chunks:
        storage.add_chunks(chunks)
        print(f"[SYSTEM] Ingested {len(chunks)} chat segments successfully.")
    else:
        # If no files found, check if there are already records in the persistent DB
        num_existing = len(getattr(storage.collection, "documents", [])) if hasattr(storage.collection, "documents") else 0
        if num_existing > 0:
            print(f"[SYSTEM] No new text files to ingest. Using {num_existing} cached chat segments.")
        else:
            print("[SYSTEM] Warning: No chat transcripts (.txt) were found in that folder, and DB is empty.")
            print("         Please ensure chat log files are present and try again.")
            
    print("\n[SYSTEM] RAG System Ready. Type '/exit' or 'exit' to quit.")
    print("-" * 65)
    
    while True:
        try:
            query = input("\nQuery: ").strip()
            if not query:
                continue
            if query.lower() in ("/exit", "exit", "quit"):
                print("Goodbye!")
                break
                
            response = run_cognitive_state_loop(storage, query)
            print("\n" + "=" * 65)
            print("RESPONSE:")
            print("-" * 65)
            print(response)
            print("=" * 65)
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\n[ERROR] An error occurred during reasoning: {e}")

if __name__ == "__main__":
    main()
